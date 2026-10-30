#!/usr/bin/env python3
"""端到端真实验收脚本（T4）：真实 uvicorn 子进程 + 真实检查点 + 前端代理。

**这不是单元测试**：脚本以子进程方式启动真实 FastAPI 服务（真实 torch 权重、
真实后台流水线线程），用 HTTP 逐项断言 `docs/API契约.md` 的关键契约，并验证
「前端生产构建 -> Vite preview -> 代理 -> 真实后端」链路。

设计要点
--------

1. **完全隔离运行期落盘**：服务子进程的 cwd 指向临时目录，并通过环境变量把
   ``VIDEOS_DIR`` / ``ARTIFACTS_DIR`` / ``CSA_SETTINGS_PATH`` 指向临时目录。
   `src/inference/batch.py` 的导出目录 ``outputs/predictions`` 是相对 cwd 的常量，
   因此批测导出的 CSV 也落在临时目录，**不会污染仓库**的 ``outputs/videos`` /
   ``outputs/predictions`` / ``data/uploads``。脚本结束前还会对仓库这几个目录做
   前后快照对比，任何新增文件都会导致失败。
2. **毫秒级流水线**：用 ``VIDEO_*_SECONDS`` / ``ARTIFACT_*_SECONDS`` 把任务时长压到
   亚秒级，整个脚本在几十秒内跑完（含真实模型加载）。
3. **只断言契约与结构，不伪造准确率**：断言 ``method`` 必须等于真实模型名
   （不能是 ``rule_placeholder``）、``probs`` 和为 1、``confidence == max(probs)``；
   好评/差评方向只用仓库真实权重实测能区分的极端样例，若方向反了如实报告实际值。
4. **前端验证尽力而为**：``--frontend`` 会用 ``npm run preview`` 起 :3000 并断言
   ``/health`` 代理到真实后端；有 Edge/Chrome 时用无头浏览器 dump DOM + 截图到
   ``outputs/e2e/frontend.png``，浏览器不可用时只做 curl 断言（不阻塞后端验收）。
5. **报告落盘**：``outputs/e2e/last_run.json``（完整证据）+ 终端 PASS/FAIL 列表；
   退出码 0 = 后端检查全部通过，1 = 有失败，3 = 环境问题（端口占用等）。

用法::

    # 后端端到端验收（自动挑空闲端口，默认 8123）
    python scripts/e2e_smoke.py

    # 追加前端链路验证（后端必须用 :8000，因为 frontend/vite.config.js 的代理
    # 目标固定为 http://localhost:8000；:3000 也由 vite preview 占用）
    python scripts/e2e_smoke.py --frontend

    # 指定端口 / 保留临时目录便于排查
    python scripts/e2e_smoke.py --port 8123 --keep-temp
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "data" / "sample"
REPORT_PATH = REPO_ROOT / "outputs" / "e2e" / "last_run.json"
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

SERVICE_NAME = "comment-sentiment-analysis-api"

#: 默认真实模型（环境变量 ``ACTIVE_MODEL`` 的默认值，见 .env.example）
DEFAULT_MODEL = "bilstm_attention"
SUPPORTED_MODELS = ("textcnn", "bilstm_attention")

#: 与模型训练语料同域（电商/外卖评论）的极端样例：真实权重实测可区分正负
POSITIVE_TEXT = "这个商品质量很好，物流也快，非常满意。"
NEGATIVE_TEXT = "质量太差了，用了一天就坏了，非常失望。"

#: 视频验收用的合法 BV 号（格式 ^BV[0-9A-Za-z]{10}$，与 tests 中一致）
VIDEO_BVID = "BV1xx411c7mD"
FRONTEND_BVID = "BV1ab411c7mE"

#: 落盘隔离：仓库里不允许被本次运行新增文件的三处运行期目录
GUARDED_REPO_DIRS = (
    REPO_ROOT / "outputs" / "videos",
    REPO_ROOT / "outputs" / "predictions",
    REPO_ROOT / "data" / "uploads",
)

#: 服务子进程的环境变量（毫秒级流水线 + 临时落盘 + 显式真实模型）
VIDEO_SECONDS = {
    "VIDEO_CRAWL_SECONDS": "0.2",
    "VIDEO_CLEAN_SECONDS": "0.1",
    "VIDEO_SENTIMENT_SECONDS": "0.1",
    "VIDEO_STATS_SECONDS": "0.1",
    "VIDEO_STEPS_PER_PHASE": "2",
}
ARTIFACT_SECONDS = {
    "ARTIFACT_CLEAN_SECONDS": "0.05",
    "ARTIFACT_SENTIMENT_SECONDS": "0.05",
    "ARTIFACT_STATS_SECONDS": "0.05",
    "ARTIFACT_STEPS_PER_STAGE": "2",
}


# --------------------------------------------------------------------------- #
# 输出 / 检查结果收集
# --------------------------------------------------------------------------- #
class CheckFailure(AssertionError):
    """断言失败（与脚本自身异常区分，报告里带证据）。"""


def brief(value: Any, limit: int = 1200) -> Any:
    """把证据裁剪成可 JSON 序列化且长度可控的形式（防止报告爆掉）。"""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): brief(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [brief(item, limit) for item in value]
    return value


class Runner:
    """按顺序执行检查项：失败不中断（后续项继续跑并各自记录），最后统一汇总。"""

    def __init__(self, section: str = "backend", fatal: bool = True) -> None:
        self.section = section
        self.fatal = fatal
        self.results: list[dict[str, Any]] = []

    def check(self, name: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            evidence = fn()
        except Exception as exc:  # noqa: BLE001 - 一项失败不能中断整轮验收
            detail = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            item = {
                "name": name,
                "section": self.section,
                "status": "FAIL",
                "detail": brief(detail),
                "evidence": None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            self.results.append(item)
            print(f"[FAIL] {name}: {detail}", flush=True)
            return None
        item = {
            "name": name,
            "section": self.section,
            "status": "PASS",
            "detail": "",
            "evidence": brief(evidence),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self.results.append(item)
        summary = ""
        if isinstance(evidence, dict) and evidence:
            keys = ("method", "label", "confidence", "accuracy", "status", "total")
            parts = [f"{k}={evidence[k]}" for k in keys if k in evidence]
            summary = " (" + ", ".join(str(p) for p in parts) + ")" if parts else ""
        print(f"[PASS] {name}{summary}", flush=True)
        return evidence

    def counts(self) -> dict[str, int]:
        passed = sum(1 for item in self.results if item["status"] == "PASS")
        return {"total": len(self.results), "passed": passed, "failed": len(self.results) - passed}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def terminate_process_tree(process: subprocess.Popen[Any], timeout: float = 20.0) -> None:
    """结束子进程及其整棵进程树。

    Windows 上 ``npm.cmd`` 会经过 cmd.exe 再拉起 node.exe，只 ``terminate()`` 父进程会留下
    仍然监听 :3000 的 node 子进程；因此 Windows 用 ``taskkill /T /F`` 整棵树一起杀。
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def snapshot_files(directory: Path) -> list[str]:
    """目录内所有文件的相对路径快照（不存在按空目录处理）。"""
    if not directory.is_dir():
        return []
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())


def assert_true(condition: Any, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def assert_close(actual: float, expected: float, *, abs_tol: float, message: str) -> None:
    if abs(float(actual) - float(expected)) > abs_tol:
        raise CheckFailure(f"{message}：实际 {actual}，期望 {expected}（容差 {abs_tol}）")


def assert_keys(exact: set[str], payload: dict[str, Any], message: str) -> None:
    """断言字段集合严格相等（契约字段一个不多一个不少）。"""
    missing = exact - set(payload)
    extra = set(payload) - exact
    assert_true(not missing and not extra, f"{message}：缺字段 {sorted(missing)}，多字段 {sorted(extra)}")


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def detect_text_column(fieldnames: list[str]) -> str:
    """与 /api/predict/batch 相同的文本列识别顺序（用于计算期望行数）。"""
    for name in ("text", "comment", "content", "评论"):
        if name in fieldnames:
            return name
    return fieldnames[0]


def expected_batch_rows(path: Path) -> int:
    rows = csv_rows(path)
    if not rows:
        return 0
    column = detect_text_column(list(rows[0].keys()))
    return sum(1 for row in rows if str(row.get(column) or "").strip())


def wait_until(
    probe: Callable[[], tuple[bool, Any]],
    *,
    timeout: float,
    interval: float = 0.2,
    description: str = "",
) -> Any:
    """轮询直到 ``probe()`` 返回 ``(True, value)``；超时抛异常，失败状态也抛异常。"""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        ok, value = probe()
        if ok:
            return value
        last = value
        time.sleep(interval)
    raise CheckFailure(f"等待超时（{timeout}s）{description}；最后状态：{brief(last, 800)}")


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
class Api:
    """极简 HTTP 封装：失败时把状态码与响应体一起放进断言消息，方便定位。"""

    def __init__(self, base_url: str, default_timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self.session = requests.Session()

    def url(self, path: str) -> str:
        return self.base_url + path

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.default_timeout)
        response = self.session.request(method, self.url(path), timeout=timeout, **kwargs)
        return response

    def expect_ok(self, response: requests.Response, where: str) -> dict[str, Any]:
        if response.status_code != 200:
            raise CheckFailure(
                f"{where} HTTP {response.status_code}：{response.text[:800]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:  # noqa: TRY301
            raise CheckFailure(f"{where} 响应不是 JSON：{response.text[:400]}") from exc
        if not isinstance(payload, dict):
            raise CheckFailure(f"{where} 响应不是对象：{payload!r}")
        return payload

    def get_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.expect_ok(self.request("GET", path, **kwargs), f"GET {path}")

    def post_json(self, path: str, payload: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.expect_ok(self.request("POST", path, json=payload, **kwargs), f"POST {path}")

    def delete_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.expect_ok(self.request("DELETE", path, **kwargs), f"DELETE {path}")


# --------------------------------------------------------------------------- #
# 单条 / 批量预测检查
# --------------------------------------------------------------------------- #
def check_predict(api: Api, text: str, expected_label: str, expected_model: str, name: str) -> dict[str, Any]:
    result = api.post_json("/api/predict", {"text": text})
    assert_keys({"text", "label", "confidence", "probs", "method"}, result, name)
    assert_true(result["text"] == text, f"{name}：text 未原样返回（{result['text']!r}）")
    probs = result["probs"]
    assert_keys({"positive", "negative"}, probs, f"{name}.probs")
    assert_true(all(0.0 <= float(v) <= 1.0 for v in probs.values()), f"{name}：概率越界 {probs}")
    assert_close(sum(probs.values()), 1.0, abs_tol=1e-6, message=f"{name}：probs 和不为 1")
    assert_true(result["label"] in {"positive", "negative"}, f"{name}：label 非法 {result['label']!r}")
    expected_by_argmax = "positive" if probs["positive"] >= probs["negative"] else "negative"
    assert_true(result["label"] == expected_by_argmax, f"{name}：label 不是 argmax（{result['label']} vs {probs}）")
    assert_close(result["confidence"], max(probs.values()), abs_tol=1e-9, message=f"{name}：confidence != max(probs)")
    assert_true(
        result["method"] not in ("", "rule_placeholder"),
        f"{name}：method 仍为规则占位（{result['method']!r}），真实权重未生效",
    )
    assert_true(
        result["method"] == expected_model,
        f"{name}：method={result['method']!r}，期望 {expected_model!r}",
    )
    assert_true(
        result["label"] == expected_label,
        f"{name}：期望 {expected_label}，实际 {result['label']}（probs={probs}，method={result['method']}）",
    )
    return result


def check_batch(api: Api, sample: Path) -> dict[str, Any]:
    content = sample.read_bytes()
    expected = expected_batch_rows(sample)
    response = api.request(
        "POST",
        "/api/predict/batch",
        files={"file": (sample.name, content, "text/csv")},
    )
    result = api.expect_ok(response, "POST /api/predict/batch")
    assert_keys({"total", "results", "export_url"}, result, "batch")
    assert_true(result["total"] == expected, f"batch.total={result['total']}，期望 {expected}")
    assert_true(isinstance(result["results"], list), "batch.results 不是数组")
    assert_true(len(result["results"]) == result["total"], "batch.results 长度与 total 不一致")
    labels = {"positive": 0, "negative": 0}
    for item in result["results"]:
        assert_keys({"index", "text", "label", "confidence"}, item, "batch.results[]")
        assert_true(item["label"] in labels, f"batch 结果 label 非法：{item['label']!r}")
        labels[item["label"]] += 1
        assert_true(0.0 <= float(item["confidence"]) <= 1.0, f"batch confidence 越界：{item['confidence']}")
    indexes = sorted(int(item["index"]) for item in result["results"])
    assert_true(indexes == list(range(len(indexes))), f"batch index 不是 0..n-1：{indexes}")
    export_url = result["export_url"]
    assert_true(isinstance(export_url, str) and export_url, "batch.export_url 为空")
    assert_true(
        re.match(r"^/api/predict/batch/download/[A-Za-z0-9_.-]+$", export_url) is not None,
        f"batch.export_url 形态不符：{export_url!r}",
    )

    download = api.request("GET", export_url)
    assert_true(download.status_code == 200, f"下载导出 CSV HTTP {download.status_code}")
    content_type = download.headers.get("Content-Type", "")
    assert_true("text/csv" in content_type, f"导出 CSV Content-Type 不是 text/csv：{content_type!r}")
    assert_true(len(download.content) > 0, "导出 CSV 为空")
    text = download.content.decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert_true(len(rows) == result["total"], f"导出 CSV 行数 {len(rows)} != total {result['total']}")
    assert_true(
        list(rows[0].keys()) == ["index", "text", "label", "confidence"] if rows else False,
        f"导出 CSV 表头不符：{list(rows[0].keys()) if rows else None}",
    )
    return {
        "total": result["total"],
        "labels": labels,
        "export_url": export_url,
        "content_type": content_type,
        "csv_bytes": len(download.content),
        "sample_result": result["results"][0],
    }


# --------------------------------------------------------------------------- #
# /api/models 检查
# --------------------------------------------------------------------------- #
def check_models(api: Api, expected_model: str) -> dict[str, Any]:
    result = api.get_json("/api/models")
    assert_keys({"models"}, result, "models")
    entries = {item.get("name"): item for item in result["models"] if isinstance(item, dict)}
    assert_true(set(SUPPORTED_MODELS) <= set(entries), f"/api/models 缺少模型：{sorted(entries)}")
    evidence: dict[str, Any] = {}
    for name in SUPPORTED_MODELS:
        item = entries[name]
        assert_keys({"name", "type", "metrics", "ready"}, item, f"models[{name}]")
        assert_true(item["ready"] is True, f"models[{name}].ready=false（检查点未就绪）")
        metrics = item["metrics"]
        assert_keys({"accuracy", "precision", "recall", "f1"}, metrics, f"models[{name}].metrics")
        assert_true(
            float(metrics["accuracy"]) > 0.8,
            f"models[{name}].accuracy={metrics['accuracy']} 不大于 0.8（真实指标未生效？）",
        )
        evidence[name] = {"ready": item["ready"], "accuracy": metrics["accuracy"], "metrics": metrics}
    active = entries.get(expected_model)
    assert_true(active is not None, f"ACTIVE_MODEL={expected_model!r} 不在 /api/models 里")
    return {"models": evidence, "expected_model": expected_model}


# --------------------------------------------------------------------------- #
# /api/videos 检查
# --------------------------------------------------------------------------- #
def check_video_flow(api: Api, expected_model: str, videos_dir: Path) -> dict[str, Any]:
    submit = api.post_json("/api/videos", {"bvid": VIDEO_BVID})
    assert_keys({"bvid", "status", "progress", "created_at"}, submit, "videos.submit")
    assert_true(submit["bvid"] == VIDEO_BVID, f"videos.submit.bvid={submit['bvid']!r}")
    assert_true(submit["status"] in ("queued", "crawling", "processing"), f"videos.submit.status={submit['status']!r}")
    assert_true(isinstance(submit["progress"], int) and 0 <= submit["progress"] <= 100, "videos.submit.progress 越界")

    def probe() -> tuple[bool, Any]:
        detail = api.get_json(f"/api/videos/{VIDEO_BVID}/detail")
        status = detail.get("analysis", {}).get("status")
        if status in ("completed", "failed"):
            return True, detail
        return False, {"status": status, "progress": detail.get("analysis", {}).get("progress")}

    detail = wait_until(probe, timeout=90, interval=0.2, description=f"视频 {VIDEO_BVID} 流水线完成")
    assert_keys(
        {"video", "analysis", "sentiment", "trend", "keywords", "model", "top_comments",
         "error", "placeholder", "pipeline"},
        detail,
        "videos.detail",
    )
    analysis = detail["analysis"]
    assert_keys({"status", "progress", "message", "length_stats"}, analysis, "videos.detail.analysis")
    assert_true(analysis["status"] == "completed", f"视频任务失败：{detail.get('error')!r}")
    assert_true(analysis["progress"] == 100, f"progress={analysis['progress']} 不是 100")
    assert_true(analysis["length_stats"] is not None, "completed 时 length_stats 为 null")

    video = detail["video"]
    assert_true(video["bvid"] == VIDEO_BVID, f"video.bvid={video['bvid']!r}")
    assert_true(int(video["comment_total"]) > 0, f"video.comment_total={video['comment_total']} 不大于 0")
    assert_true(bool(video["title"]), "video.title 为空")

    sentiment = detail["sentiment"]
    assert_true(sentiment is not None, "completed 时 sentiment 为 null")
    for key in ("total", "positive", "negative", "neutral"):
        assert_true(int(sentiment[key]) >= 0, f"sentiment.{key} 非法：{sentiment[key]!r}")
    assert_true(int(sentiment["total"]) > 0, f"sentiment.total={sentiment['total']} 不大于 0")
    assert_true(
        int(sentiment["positive"]) + int(sentiment["negative"]) + int(sentiment["neutral"])
        == int(sentiment["total"]),
        f"sentiment 计数不自洽：{sentiment}",
    )
    assert_close(
        sentiment["positive_ratio"] + sentiment["negative_ratio"] + sentiment["neutral_ratio"],
        1.0,
        abs_tol=0.01,
        message="sentiment 三项占比和不为 1",
    )

    model = detail["model"]
    assert_keys({"name", "accuracy", "precision", "recall", "f1"}, model, "videos.detail.model")
    assert_true(model["name"] == expected_model, f"detail.model.name={model['name']!r}，期望 {expected_model!r}")
    assert_true(float(model["accuracy"]) > 0.8, f"detail.model.accuracy={model['accuracy']} 不大于 0.8")

    placeholder = detail["placeholder"]
    assert_true(isinstance(placeholder, bool), f"detail.placeholder 不是布尔：{placeholder!r}")
    # 本次运行未开真实爬虫（VIDEO_REAL_CRAWL=0），评论数据是占位数据 → placeholder 必须为 true（诚实标注）
    assert_true(placeholder is True, "占位数据路径下 detail.placeholder 应为 true（诚实标注）")

    assert_true(isinstance(detail["trend"], list) and detail["trend"], "detail.trend 为空")
    assert_true(isinstance(detail["keywords"].get("positive"), list), "detail.keywords 结构不符")
    assert_true(isinstance(detail["top_comments"].get("positive"), list), "detail.top_comments 结构不符")

    # 落盘隔离：VIDEOS_DIR 环境变量生效，任务写进临时目录而不是 outputs/videos
    task_file = videos_dir / f"{VIDEO_BVID}.json"
    index_file = videos_dir / "index.json"
    assert_true(task_file.is_file(), f"任务未写入 VIDEOS_DIR 临时目录：{task_file}")
    assert_true(index_file.is_file(), f"索引未写入 VIDEOS_DIR：{index_file}")

    deleted = api.delete_json(f"/api/videos/{VIDEO_BVID}")
    assert_keys({"deleted"}, deleted, "videos.delete")
    assert_true(deleted["deleted"] is True, "DELETE /api/videos 未返回 deleted=true")
    gone = api.request("GET", f"/api/videos/{VIDEO_BVID}/detail")
    assert_true(gone.status_code == 404, f"删除后 detail 应为 404，实际 {gone.status_code}")

    return {
        "bvid": VIDEO_BVID,
        "status": analysis["status"],
        "sentiment": sentiment,
        "model": model,
        "placeholder": placeholder,
        "comment_total": video["comment_total"],
        "videos_dir": str(videos_dir),
        "pipeline": detail.get("pipeline", {}).get("crawl", {}),
    }


# --------------------------------------------------------------------------- #
# /api/artifacts 检查（真实模型最终证据：上传 CSV -> 处理 -> analysis.method）
# --------------------------------------------------------------------------- #
def check_artifacts_flow(
    api: Api,
    sample: Path,
    expected_model: str,
    uploads_dir: Path,
) -> dict[str, Any]:
    source_rows = len(csv_rows(sample))
    content = sample.read_bytes()
    response = api.request(
        "POST",
        "/api/artifacts/upload",
        files={"files": (sample.name, content, "text/csv")},
        data={"name": "E2E 验收数据"},
    )
    project = api.expect_ok(response, "POST /api/artifacts/upload")
    assert_keys(
        {"id", "name", "created_at", "status", "progress", "message", "sources",
         "stages", "sentiment", "method", "error"},
        project,
        "artifacts.project",
    )
    project_id = project["id"]
    assert_true(
        re.match(r"^p_\d{8}_\d{4}_[0-9a-f]{6}$", str(project_id)) is not None,
        f"项目 id 形态不符：{project_id!r}",
    )
    assert_true(project["name"] == "E2E 验收数据", f"项目 name={project['name']!r}")
    assert_true(len(project["sources"]) == 1, f"sources 数量异常：{project['sources']}")
    assert_true(int(project["sources"][0]["rows"]) == source_rows, f"sources[0].rows={project['sources'][0]['rows']}，期望 {source_rows}")

    def probe() -> tuple[bool, Any]:
        current = api.get_json(f"/api/artifacts/{project_id}")
        if current.get("status") in ("completed", "failed"):
            return True, current
        return False, {"status": current.get("status"), "progress": current.get("progress"), "message": current.get("message")}

    completed = wait_until(probe, timeout=120, interval=0.2, description=f"项目 {project_id} 处理完成")
    assert_true(completed["status"] == "completed", f"数据项目失败：{completed.get('error')!r}")
    assert_true(completed["progress"] == 100, f"项目 progress={completed['progress']} 不是 100")
    assert_true(completed.get("error") is None, f"completed 项目仍有 error：{completed.get('error')!r}")

    method = completed.get("method")
    assert_true(method not in (None, "", "rule_placeholder"), f"项目 method 仍为规则占位：{method!r}")
    assert_true(method == expected_model, f"项目 method={method!r}，期望 {expected_model!r}")

    sentiment = completed["sentiment"]
    assert_true(sentiment is not None, "completed 时项目 sentiment 为 null")
    assert_true(int(sentiment["total"]) > 0, f"项目 sentiment.total={sentiment['total']} 不大于 0")
    stages = {item["key"]: item for item in completed["stages"]}
    assert_true(set(stages) == {"initial", "clean", "excluded"}, f"项目 stages={sorted(stages)}")
    assert_true(int(stages["initial"]["rows"]) == source_rows, f"initial.rows={stages['initial']['rows']}，期望 {source_rows}")
    clean_rows = int(stages["clean"]["rows"])
    assert_true(clean_rows > 0, "clean.rows 为 0（有效评论为空）")
    assert_true(int(stages["clean"]["columns"]) > 0, "clean.columns 为 0")

    # 落盘隔离：项目写进 ARTIFACTS_DIR 临时目录，且 sentiment.csv 的 method 列是模型名
    project_dir = uploads_dir / str(project_id)
    sentiment_csv = project_dir / "sentiment.csv"
    assert_true(project_dir.is_dir(), f"项目未写入 ARTIFACTS_DIR 临时目录：{project_dir}")
    assert_true(sentiment_csv.is_file(), f"缺少 sentiment.csv：{sentiment_csv}")
    sentiment_rows = csv_rows(sentiment_csv)
    assert_true(len(sentiment_rows) == clean_rows, f"sentiment.csv 行数 {len(sentiment_rows)} != clean.rows {clean_rows}")
    sentiment_methods = {row.get("method") for row in sentiment_rows}
    assert_true(
        sentiment_methods == {expected_model},
        f"sentiment.csv method 列={sentiment_methods}，期望仅 {expected_model!r}",
    )

    # --- /stages ---
    stages_payload = api.get_json(f"/api/artifacts/{project_id}/stages")
    assert_keys({"stages"}, stages_payload, "artifacts.stages")
    assert_true(len(stages_payload["stages"]) == 3, f"stages 数量 {len(stages_payload['stages'])} != 3")
    stage_keys = [item["key"] for item in stages_payload["stages"]]
    assert_true(stage_keys == ["initial", "clean", "excluded"], f"stages 顺序/枚举不符：{stage_keys}")

    # --- /table ---
    table = api.get_json(f"/api/artifacts/{project_id}/table?stage=clean&limit=100")
    assert_keys({"stage", "columns", "rows", "total_rows", "offset", "limit", "encoding"}, table, "artifacts.table")
    assert_true(table["stage"] == "clean", f"table.stage={table['stage']!r}")
    assert_true(table["encoding"] == "utf-8-sig", f"table.encoding={table['encoding']!r}")
    assert_true(int(table["total_rows"]) == clean_rows, f"table.total_rows={table['total_rows']} != clean.rows {clean_rows}")
    assert_true(len(table["columns"]) > 0, "table.columns 为空")
    assert_true(len(table["rows"]) > 0, "table.rows 为空")

    # --- /comments ---
    comments = api.get_json(f"/api/artifacts/{project_id}/comments?limit=200")
    assert_keys({"total", "offset", "limit", "items"}, comments, "artifacts.comments")
    assert_true(int(comments["total"]) == clean_rows, f"comments.total={comments['total']} != clean.rows {clean_rows}")
    assert_true(len(comments["items"]) > 0, "comments.items 为空")
    labels = {"positive": 0, "negative": 0, "neutral": 0}
    for item in comments["items"]:
        assert_keys({"index", "text", "label", "confidence", "probs"}, item, "artifacts.comments.items[]")
        assert_true(item["label"] in labels, f"comments label 非法：{item['label']!r}")
        labels[item["label"]] += 1
        assert_true(bool(str(item["text"]).strip()), "comments item.text 为空")
        probs = item["probs"]
        assert_keys({"positive", "negative"}, probs, "artifacts.comments.probs")
        assert_close(sum(probs.values()), 1.0, abs_tol=1e-6, message="comments.probs 和不为 1")
        assert_close(item["confidence"], max(probs.values()), abs_tol=1e-6, message="comments.confidence != max(probs)")
    positive_only = api.get_json(f"/api/artifacts/{project_id}/comments?label=positive&limit=10")
    assert_true(
        all(item["label"] == "positive" for item in positive_only["items"]),
        "label=positive 过滤后仍出现其它极性",
    )

    # --- /analysis ---
    analysis = api.get_json(f"/api/artifacts/{project_id}/analysis")
    assert_keys({"method", "total", "sentiment", "keywords", "length", "sample"}, analysis, "artifacts.analysis")
    assert_true(
        analysis["method"] == expected_model,
        f"analysis.method={analysis['method']!r}，期望 {expected_model!r}（真实模型未接入数据项目？）",
    )
    assert_true(int(analysis["total"]) == clean_rows, f"analysis.total={analysis['total']} != clean.rows {clean_rows}")
    agg = analysis["sentiment"]
    assert_keys(
        {"positive", "negative", "neutral", "positive_ratio", "negative_ratio", "neutral_ratio"},
        agg,
        "artifacts.analysis.sentiment",
    )
    assert_true(
        int(agg["positive"]) + int(agg["negative"]) + int(agg["neutral"]) == int(analysis["total"]),
        f"analysis 计数不自洽：{agg}",
    )
    assert_close(
        agg["positive_ratio"] + agg["negative_ratio"] + agg["neutral_ratio"],
        1.0,
        abs_tol=0.01,
        message="analysis 三项占比和不为 1",
    )
    assert_true(bool(analysis["sample"]), "analysis.sample 为空")
    assert_true(isinstance(analysis["keywords"]["positive"], list), "analysis.keywords 结构不符")

    # --- /path（只读）确认路径位于隔离目录内 ---
    path_payload = api.get_json(f"/api/artifacts/{project_id}/path?stage=clean")
    assert_keys({"stage", "path", "exists"}, path_payload, "artifacts.path")
    resolved = Path(path_payload["path"]).resolve()
    assert_true(path_payload["exists"] is True, "path.exists 为 false（clean.csv 应已生成）")
    assert_true(
        resolved.is_relative_to(uploads_dir.resolve()),
        f"clean.csv 路径不在隔离的 ARTIFACTS_DIR 内：{resolved}（隔离目录 {uploads_dir}）",
    )

    deleted = api.delete_json(f"/api/artifacts/{project_id}")
    assert_keys({"deleted"}, deleted, "artifacts.delete")
    assert_true(deleted["deleted"] is True, "DELETE /api/artifacts 未返回 deleted=true")
    gone = api.request("GET", f"/api/artifacts/{project_id}")
    assert_true(gone.status_code == 404, f"删除后项目应为 404，实际 {gone.status_code}")

    return {
        "project_id": project_id,
        "method": method,
        "analysis_method": analysis["method"],
        "total": analysis["total"],
        "sentiment": agg,
        "labels": labels,
        "table_columns": table["columns"][:12],
        "table_total_rows": table["total_rows"],
        "sentiment_csv_methods": sorted(sentiment_methods),
        "uploads_dir": str(uploads_dir),
        "sample": analysis["sample"][0],
    }


# --------------------------------------------------------------------------- #
# 隔离检查 + 权重事实
# --------------------------------------------------------------------------- #
def check_no_repo_pollution(before: dict[str, list[str]]) -> dict[str, Any]:
    after = {str(path): snapshot_files(path) for path in GUARDED_REPO_DIRS}
    diffs = {}
    for key, old in before.items():
        new = after.get(key, [])
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        if added or removed:
            diffs[key] = {"added": added[:20], "removed": removed[:20]}
    assert_true(not diffs, f"仓库运行期目录被本次验收污染：{diffs}")
    return {"guarded_dirs": [str(path) for path in GUARDED_REPO_DIRS], "unchanged": True}


def collect_model_facts(expected_model: str) -> dict[str, Any]:
    """记录真实权重事实（文件大小 / 词表规模 / metrics 准确率），防止「小冒烟权重」冒充。"""
    facts: dict[str, Any] = {"expected_model": expected_model, "checkpoints": {}}
    metrics_dir = REPO_ROOT / "outputs" / "stats"
    for name in SUPPORTED_MODELS:
        checkpoint = REPO_ROOT / "models" / "checkpoints" / name / "best.pt"
        metadata_path = REPO_ROOT / "models" / "checkpoints" / name / "metadata.json"
        vocab_size = None
        val_acc = None
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                vocab_size = metadata.get("hyperparams", {}).get("vocab_size")
                val_acc = metadata.get("metrics", {}).get("val_acc")
            except (json.JSONDecodeError, OSError):
                pass
        metrics = {}
        metrics_path = metrics_dir / f"metrics_{name}.json"
        if metrics_path.is_file():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8")).get("metrics", {})
            except (json.JSONDecodeError, OSError):
                pass
        facts["checkpoints"][name] = {
            "best_pt_bytes": file_size(checkpoint),
            "best_pt_exists": checkpoint.is_file(),
            "metadata_vocab_size": vocab_size,
            "metadata_val_acc": val_acc,
            "test_accuracy": metrics.get("accuracy"),
            "test_f1": metrics.get("f1"),
        }
    active = facts["checkpoints"].get(expected_model, {})
    if active.get("metadata_vocab_size") is not None and int(active["metadata_vocab_size"]) < 1000:
        facts["weights_note"] = "冒烟小权重（vocab_size<1000）：只覆盖加载与推理链路，不代表准确率"
    else:
        facts["weights_note"] = (
            f"真实训练权重已回填（{expected_model} vocab_size={active.get('metadata_vocab_size')}，"
            f"测试 accuracy={active.get('test_accuracy')}）；模型训练语料为电商/外卖/酒店评论，"
            "不等价于 B 站领域准确率"
        )
    return facts


# --------------------------------------------------------------------------- #
# 前端链路验证（尽力而为，失败不阻塞后端验收）
# --------------------------------------------------------------------------- #
FRONTEND_ERROR_TEXTS = (
    "无法连接后端",
    "后端未启动",
    "待接入后端",
    "接口请求失败",
    "Network Error",
)


def find_browser() -> str | None:
    """找一个可用的无头 Chromium 内核浏览器（Edge / Chrome），找不到返回 None。"""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/microsoft-edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def browser_dump_dom(browser: str, url: str, timeout: float = 60.0) -> str:
    """用无头浏览器 dump 渲染后的 DOM（--dump-dom 会等页面 JS 执行完）。"""
    profile = Path(tempfile.mkdtemp(prefix="csa_e2e_browser_"))
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--virtual-time-budget=8000",
        f"--user-data-dir={profile}",
        "--dump-dom",
        url,
    ]
    try:
        process = subprocess.run(
            command, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        return process.stdout or ""
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def browser_screenshot(browser: str, url: str, output: Path, timeout: float = 60.0) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    profile = Path(tempfile.mkdtemp(prefix="csa_e2e_browser_"))
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--hide-scrollbars",
        "--window-size=1440,900",
        "--virtual-time-budget=8000",
        f"--user-data-dir={profile}",
        f"--screenshot={output}",
        url,
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=timeout)
        return output.is_file() and output.stat().st_size > 10_000
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def run_frontend_checks(
    backend_port: int,
    backend_api: Api,
    expected_model: str,
    temp_root: Path,
    frontend_port: int,
) -> dict[str, Any]:
    """启动 ``npm run preview -- --port <port>``，断言首页与 /health 代理到真实后端。"""
    runner = Runner(section="frontend", fatal=False)
    preview_log = temp_root / "frontend_preview.log"
    report: dict[str, Any] = {
        "requested": True,
        "base_url": f"http://127.0.0.1:{frontend_port}",
        "port": frontend_port,
        "checks": runner.results,
        "screenshot": None,
        "browser": None,
    }
    if backend_port != 8000:
        report["skipped"] = (
            f"前端代理目标固定为 http://localhost:8000，但后端端口是 {backend_port}；"
            "请用 --frontend（默认后端 :8000）运行"
        )
        runner.results.append({"name": "frontend_skip", "section": "frontend", "status": "WARN",
                               "detail": report["skipped"], "evidence": None, "duration_ms": 0})
        print(f"[WARN] 前端验证跳过：{report['skipped']}", flush=True)
        return report

    if not FRONTEND_DIST.is_dir():
        report["skipped"] = "frontend/dist 不存在，请先在 frontend/ 下执行 npm run build"
        runner.results.append({"name": "frontend_skip", "section": "frontend", "status": "WARN",
                               "detail": report["skipped"], "evidence": None, "duration_ms": 0})
        print(f"[WARN] 前端验证跳过：{report['skipped']}", flush=True)
        return report
    if not port_is_free(frontend_port):
        report["skipped"] = f":{frontend_port} 已被其它进程占用（不打扰外部进程，跳过前端验证）"
        runner.results.append({"name": "frontend_skip", "section": "frontend", "status": "WARN",
                               "detail": report["skipped"], "evidence": None, "duration_ms": 0})
        print(f"[WARN] 前端验证跳过：{report['skipped']}", flush=True)
        return report

    # npm 在 Windows 上是 npm.cmd
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        report["skipped"] = "找不到 npm 可执行文件"
        runner.results.append({"name": "frontend_skip", "section": "frontend", "status": "WARN",
                               "detail": report["skipped"], "evidence": None, "duration_ms": 0})
        print(f"[WARN] 前端验证跳过：{report['skipped']}", flush=True)
        return report

    env = dict(os.environ)
    # Node 24 在 Windows 上把 localhost 优先解析为 ::1；后端监听 127.0.0.1，
    # 因此 preview 进程内强制 IPv4 优先，代理才能连上真实后端。
    env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --dns-result-order=ipv4first").strip()
    env["BROWSER"] = "none"
    preview = subprocess.Popen(
        [npm, "run", "preview", "--", "--port", str(frontend_port), "--host", "127.0.0.1"],
        cwd=str(FRONTEND_DIR),
        env=env,
        stdout=preview_log.open("w", encoding="utf-8", errors="replace"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{frontend_port}"

    def wait_preview() -> None:
        wait_until(
            lambda: _probe_http(f"{base}/"),
            timeout=90,
            interval=0.3,
            description="Vite preview :3000 启动",
        )

    def _probe_http(url: str) -> tuple[bool, Any]:
        try:
            response = requests.get(url, timeout=5)
        except requests.RequestException as exc:
            return False, {"error": str(exc)}
        return response.status_code < 500, {"status": response.status_code}

    try:
        runner.check("frontend_preview_up", lambda: (wait_preview(), {"url": base})[1])

        def check_index() -> dict[str, Any]:
            response = requests.get(f"{base}/", timeout=15)
            assert_true(response.status_code == 200, f"首页 HTTP {response.status_code}")
            assert_true("text/html" in response.headers.get("Content-Type", ""), "首页 Content-Type 不是 text/html")
            # Vite 产物固定 UTF-8；requests 对无 charset 的 text/html 会猜错编码，这里显式解码
            html = response.content.decode("utf-8", errors="replace")
            assert_true('<div id="app">' in html or "<div id='app'>" in html, "首页 HTML 缺少 #app 挂载点")
            assert_true("/assets/" in html, "首页 HTML 没有引用构建产物 /assets/")
            title_match = re.search(r"<title>(.*?)</title>", html, re.S)
            return {
                "status": response.status_code,
                "bytes": len(response.content),
                "title": title_match.group(1).strip() if title_match else None,
            }

        runner.check("frontend_index_html", check_index)

        def check_proxy_health() -> dict[str, Any]:
            response = requests.get(f"{base}/health", timeout=15)
            assert_true(response.status_code == 200, f":3000/health 代理 HTTP {response.status_code}（后端未接通）")
            payload = response.json()
            assert_true(payload.get("status") == "ok", f":3000/health.status={payload.get('status')!r}")
            assert_true(
                payload.get("service") == SERVICE_NAME,
                f":3000/health.service={payload.get('service')!r}，期望真实后端 {SERVICE_NAME!r}（mock 是否未关闭？）",
            )
            return payload

        runner.check("frontend_proxy_health", check_proxy_health)

        # 静态检查 T3：默认 mock 关闭（只有显式 VITE_USE_MOCK=1 才启用）
        def check_mock_off() -> dict[str, Any]:
            config_text = (FRONTEND_DIR / "vite.config.js").read_text(encoding="utf-8")
            assert_true(
                "process.env.VITE_USE_MOCK === '1'" in config_text,
                "vite.config.js 未采用『默认关闭 mock』写法（VITE_USE_MOCK === '1'）",
            )
            assert_true(
                "process.env.VITE_USE_MOCK !== '0'" not in config_text,
                "vite.config.js 仍是旧的『默认开启 mock』写法（VITE_USE_MOCK !== '0'）",
            )
            assert_true("mockApiPlugin()" in config_text, "vite.config.js 缺少 mock 插件引用")
            return {"default_mock": "off", "enable_flag": "VITE_USE_MOCK=1"}

        runner.check("frontend_mock_default_off", check_mock_off)

        # 让真实后端里出现一个视频任务，用浏览器断言左栏真的渲染了后端数据
        created = False
        try:
            backend_api.post_json("/api/videos", {"bvid": FRONTEND_BVID})
            created = True
            wait_until(
                lambda: _video_completed(backend_api, FRONTEND_BVID),
                timeout=60,
                interval=0.2,
                description=f"前端验收用视频 {FRONTEND_BVID} 完成",
            )
        except Exception as exc:  # noqa: BLE001 - 前端验收不阻塞
            print(f"[WARN] 前端验收用视频任务创建/等待失败：{exc}", flush=True)

        browser = find_browser()
        report["browser"] = browser

        def check_browser_dom() -> dict[str, Any]:
            assert_true(browser is not None, "未找到 Edge/Chrome，无法做无头浏览器 DOM 断言（curl 验证已通过）")
            dom = browser_dump_dom(browser, f"{base}/")
            assert_true(len(dom) > 2000, f"无头浏览器 DOM 过短（{len(dom)} 字节）")
            lowered = dom
            assert_true("B 站视频评论情感分析系统" in lowered, "页面标题/品牌文案缺失")
            assert_true(
                "bv-input__field" in lowered and "BV1xx411c7mD" in lowered,
                "页面缺少 BV 号输入框（真实页面未渲染）",
            )
            assert_true("数据项目" in lowered and "单独评论" in lowered and "视频任务" in lowered,
                        "页面缺少左栏三段（数据项目/单独评论/视频任务）")
            assert_true("bili__statusbar" in lowered, "页面缺少底部状态栏")
            for error_text in FRONTEND_ERROR_TEXTS:
                assert_true(error_text not in lowered, f"页面出现后端未接通错误提示：{error_text!r}")
            if created:
                assert_true(
                    FRONTEND_BVID in lowered,
                    f"页面左栏未渲染真实后端的视频任务 {FRONTEND_BVID}（mock 未关闭或请求失败？）",
                )
            return {
                "dom_bytes": len(dom),
                "has_bv_input": True,
                "has_sidebar_sections": True,
                "renders_backend_video": created and FRONTEND_BVID in lowered,
            }

        runner.check("frontend_browser_dom", check_browser_dom)

        def check_screenshot() -> dict[str, Any]:
            assert_true(browser is not None, "未找到 Edge/Chrome，无法截图")
            output = REPORT_PATH.parent / "frontend.png"
            ok = browser_screenshot(browser, f"{base}/", output)
            assert_true(ok, f"无头浏览器截图失败或文件过小：{output}")
            report["screenshot"] = str(output)
            return {"path": str(output), "bytes": output.stat().st_size}

        runner.check("frontend_screenshot", check_screenshot)

        if created:
            try:
                backend_api.delete_json(f"/api/videos/{FRONTEND_BVID}")
            except Exception:  # noqa: BLE001 - 清理失败不影响结论
                pass
    finally:
        terminate_process_tree(preview)
        try:
            preview_log.close()
        except Exception:  # noqa: BLE001
            pass

    counts = runner.counts()
    report["counts"] = counts
    print(
        f"[frontend] {counts['passed']}/{counts['total']} PASS"
        + (f", {counts['failed']} FAIL" if counts["failed"] else ""),
        flush=True,
    )
    return report


def _video_completed(api: Api, bvid: str) -> tuple[bool, Any]:
    response = api.request("GET", f"/api/videos/{bvid}/detail")
    if response.status_code != 200:
        return False, {"status": response.status_code}
    payload = response.json()
    status = payload.get("analysis", {}).get("status")
    return status in ("completed", "failed"), {"status": status}


# --------------------------------------------------------------------------- #
# 服务子进程管理
# --------------------------------------------------------------------------- #
class BackendServer:
    """以子进程方式在隔离目录中启动真实 uvicorn 服务。"""

    def __init__(self, port: int, temp_root: Path, active_model: str) -> None:
        self.port = port
        self.temp_root = temp_root
        self.active_model = active_model
        self.base_url = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen[Any] | None = None
        self.log_path = temp_root / "uvicorn.log"
        self.videos_dir = temp_root / "videos"
        self.uploads_dir = temp_root / "uploads"
        self.settings_path = temp_root / "settings.json"
        self._log_handle: Any = None

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + pythonpath if pythonpath else "")
        env.update(VIDEO_SECONDS)
        env.update(ARTIFACT_SECONDS)
        env["VIDEOS_DIR"] = str(self.videos_dir)
        env["ARTIFACTS_DIR"] = str(self.uploads_dir)
        env["CSA_SETTINGS_PATH"] = str(self.settings_path)
        env["ACTIVE_MODEL"] = self.active_model
        env["INFERENCE_DEVICE"] = "cpu"
        env["VIDEO_REAL_CRAWL"] = "0"  # 不联网；占位评论数据（detail.placeholder=true）
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def start(self) -> None:
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("w", encoding="utf-8", errors="replace")
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "src.inference.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(self.temp_root),  # 隔离 cwd：batch 导出等相对 outputs/ 路径也落进临时目录
            env=self.env(),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        wait_until(
            self._health_probe,
            timeout=120,
            interval=0.25,
            description=f"uvicorn :{self.port} 启动",
        )

    def _health_probe(self) -> tuple[bool, Any]:
        if self.process is not None and self.process.poll() is not None:
            raise CheckFailure(
                f"uvicorn 提前退出（exit={self.process.returncode}）：{self.tail_log(2000)}"
            )
        try:
            response = requests.get(f"{self.base_url}/health", timeout=3)
        except requests.RequestException as exc:
            return False, {"error": str(exc)}
        if response.status_code == 200:
            return True, response.json()
        return False, {"status": response.status_code}

    def tail_log(self, limit: int = 4000) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-limit:]

    def stop(self) -> None:
        if self.process is not None:
            terminate_process_tree(self.process)
            self.process = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="端到端真实验收：真实 uvicorn + 真实检查点 + 前端代理（详见模块 docstring）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="后端端口；默认 --frontend 用 8000，否则用 8123（被占用时自动挑空闲端口）",
    )
    parser.add_argument("--frontend", action="store_true", help="追加前端 :3000 代理 + 无头浏览器验证（需后端 :8000）")
    parser.add_argument(
        "--frontend-port",
        type=int,
        default=3000,
        help="Vite preview 端口（默认 3000；被占用时自动改用空闲端口，不会打扰外部进程）",
    )
    parser.add_argument("--activation-model", default=None, help=f"覆盖 ACTIVE_MODEL（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时目录（排查失败用）")
    parser.add_argument("--report", default=str(REPORT_PATH), help=f"报告输出路径（默认 {REPORT_PATH}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 终端输出统一 UTF-8（Windows 默认 cp936 会让中文 PASS/FAIL 列表变乱码；
    # 报告 JSON 本身固定 UTF-8 落盘，不受影响）
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    args = build_arg_parser().parse_args(argv)
    expected_model = args.activation_model or os.environ.get("E2E_ACTIVE_MODEL") or DEFAULT_MODEL
    expected_model = expected_model.strip() or DEFAULT_MODEL
    if expected_model not in SUPPORTED_MODELS:
        print(f"[ERR] ACTIVE_MODEL={expected_model!r} 不在支持列表 {SUPPORTED_MODELS} 中", flush=True)
        return 3

    requested_port = args.port or (8000 if args.frontend else 8123)
    if requested_port == 3000:
        print("[ERR] 后端不能用 3000（前端 preview 占用），请换端口", flush=True)
        return 3
    if not port_is_free(requested_port):
        if args.port or args.frontend:
            print(
                f"[ERR] 端口 {requested_port} 已被占用（前端验收固定需要后端 :8000，"
                "不会去动别人的进程；请先释放）",
                flush=True,
            )
            return 3
        requested_port = free_port()
        print(f"[WARN] 默认端口 8123 被占用，改用空闲端口 {requested_port}", flush=True)

    frontend_port = args.frontend_port
    if args.frontend and not port_is_free(frontend_port):
        frontend_port = free_port()
        print(
            f"[WARN] 前端端口 {args.frontend_port} 被占用（不动别人的进程），"
            f"改用空闲端口 {frontend_port}",
            flush=True,
        )

    temp_root = Path(tempfile.mkdtemp(prefix="csa_e2e_"))
    report_file = Path(args.report).resolve()
    started_at = now_iso()
    clock = time.perf_counter()
    print("=" * 78)
    print("端到端真实验收（T4）：真实 uvicorn + 真实检查点 + 契约断言")
    print(f"  仓库        : {REPO_ROOT}")
    print(f"  后端        : http://127.0.0.1:{requested_port}")
    print(f"  活跃模型    : {expected_model}")
    print(f"  临时落盘    : {temp_root}")
    print(
        f"  前端验证    : "
        f"{f'开启（Vite preview :{frontend_port}）' if args.frontend else '关闭（--frontend 开启）'}"
    )
    print("=" * 78, flush=True)

    before_snapshot = {str(path): snapshot_files(path) for path in GUARDED_REPO_DIRS}

    server = BackendServer(requested_port, temp_root, expected_model)
    runner = Runner(section="backend", fatal=True)
    frontend_report: dict[str, Any] | None = None
    fatal_error: str | None = None
    exit_code = 1
    try:
        server.start()
        api = Api(server.base_url)

        runner.check(
            "health",
            lambda: _check_health(api),
        )
        runner.check(
            "predict_positive",
            lambda: check_predict(api, POSITIVE_TEXT, "positive", expected_model, "predict_positive"),
        )
        runner.check(
            "predict_negative",
            lambda: check_predict(api, NEGATIVE_TEXT, "negative", expected_model, "predict_negative"),
        )
        runner.check("models", lambda: check_models(api, expected_model))
        runner.check(
            "predict_batch",
            lambda: check_batch(api, SAMPLE_DIR / "test_sample.csv"),
        )
        runner.check(
            "videos_flow",
            lambda: check_video_flow(api, expected_model, server.videos_dir),
        )
        runner.check(
            "artifacts_flow",
            lambda: check_artifacts_flow(
                api,
                SAMPLE_DIR / "bilibili_demo.csv",
                expected_model,
                server.uploads_dir,
            ),
        )
        runner.check(
            "repo_isolation",
            lambda: check_no_repo_pollution(before_snapshot),
        )

        # 附录事实：真实权重规模（防止冒烟小权重冒充真实权重）
        try:
            model_facts = collect_model_facts(expected_model)
            runner.results.append(
                {
                    "name": "model_weights_facts",
                    "section": "backend",
                    "status": "PASS",
                    "detail": model_facts["weights_note"],
                    "evidence": brief(model_facts),
                    "duration_ms": 0,
                }
            )
            print(f"[PASS] model_weights_facts: {model_facts['weights_note']}", flush=True)
        except Exception as exc:  # noqa: BLE001 - 附录信息不阻塞
            print(f"[WARN] 读取权重事实失败：{exc}", flush=True)

        if args.frontend:
            frontend_report = run_frontend_checks(
                requested_port, api, expected_model, temp_root, frontend_port
            )

        counts = runner.counts()
        exit_code = 0 if counts["failed"] == 0 else 1
    except Exception as exc:  # noqa: BLE001 - 顶层异常也必须落报告
        fatal_error = f"{type(exc).__name__}: {exc}"
        exit_code = 1
        print(f"[FATAL] {fatal_error}", flush=True)
    finally:
        server.stop()
        if not args.keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)

    counts = runner.counts()
    duration = round(time.perf_counter() - clock, 2)
    report = {
        "name": "T4 端到端真实验收",
        "generated_at": now_iso(),
        "started_at": started_at,
        "duration_seconds": duration,
        "repo_root": str(REPO_ROOT),
        "python": sys.version.split()[0],
        "backend": {
            "base_url": server.base_url,
            "port": requested_port,
            "active_model": expected_model,
            "uvicorn_log_tail": server.tail_log(2000),
        },
        "frontend_requested": bool(args.frontend),
        "frontend_port": frontend_port if args.frontend else None,
        "checks": runner.results,
        "summary": {
            **counts,
            "result": "PASS" if counts["failed"] == 0 else "FAIL",
            "exit_code": exit_code,
        },
        "frontend": frontend_report,
        "fatal_error": fatal_error,
        "weights": collect_model_facts(expected_model) if fatal_error is None else None,
        "notes": [
            "模型训练语料为电商/外卖评论（waimai_10k + ChnSentiCorp 酒店评论），不等价于 B 站领域准确率。",
            "任务/项目落盘全部指向临时目录（VIDEOS_DIR / ARTIFACTS_DIR / CSA_SETTINGS_PATH），"
            "仓库 outputs/videos、outputs/predictions、data/uploads 前后快照一致。",
            "本次 /api/videos 未开启真实爬虫（VIDEO_REAL_CRAWL=0），detail.placeholder=true 表示评论为占位数据；"
            "视频流水线的情感推理真实模型路径由 tests/test_videos_real_model.py 覆盖。",
        ],
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=" * 78)
    print(f"后端检查：{counts['passed']}/{counts['total']} PASS"
          + (f"，{counts['failed']} FAIL" if counts["failed"] else ""))
    if frontend_report is not None:
        f_counts = frontend_report.get("counts", {})
        print(f"前端验证：{f_counts.get('passed', 0)}/{f_counts.get('total', 0)} PASS"
              + (f"，{f_counts.get('failed', 0)} FAIL" if f_counts.get("failed") else ""))
        if frontend_report.get("screenshot"):
            print(f"前端截图：{frontend_report['screenshot']}")
        if frontend_report.get("skipped"):
            print(f"前端说明：{frontend_report['skipped']}")
    print(f"耗时：{duration}s")
    print(f"报告：{report_file}")
    print(f"结果：{'PASS' if exit_code == 0 else 'FAIL'}")
    print("=" * 78, flush=True)

    if exit_code != 0:
        print("\n失败项：", flush=True)
        for item in runner.results:
            if item["status"] == "FAIL":
                print(f"  - {item['name']}: {item['detail']}", flush=True)
        if fatal_error:
            print(f"  - FATAL: {fatal_error}", flush=True)
    return exit_code


def _check_health(api: Api) -> dict[str, Any]:
    payload = api.get_json("/health")
    assert_keys({"status", "service", "version"}, payload, "health")
    assert_true(payload["status"] == "ok", f"health.status={payload['status']!r}")
    assert_true(payload["service"] == SERVICE_NAME, f"health.service={payload['service']!r}")
    assert_true(bool(str(payload["version"]).strip()), "health.version 为空")
    return payload


if __name__ == "__main__":
    sys.exit(main())
