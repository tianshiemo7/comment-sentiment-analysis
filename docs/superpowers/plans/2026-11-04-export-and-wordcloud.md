# 数据导出 + 词云 60 + Python 词云图 实现计划（2026-11-04）

> **For agentic workers:** 本计划由 Lead 派发给队友并行实现；每个任务自成可测试交付物。
> 步骤用 `- [ ]` 勾选跟踪。接口以
> [`docs/superpowers/specs/2026-11-04-export-and-wordcloud-design.md`](../specs/2026-11-04-export-and-wordcloud-design.md)
> 为准，偏离先通知 Lead。

**Goal:** 数据项目/视频任务可导出爬虫原始与清洗后 CSV；词云每侧 60 词；词云可切换 Python 渲染 PNG。

**Architecture:** 导出走只读下载端点（后端从落盘 CSV/JSON 返回，不新增路径拼接）；Python 词云在
`src/visualization/wordclouds.py` 新增无 matplotlib 的 `WordCloud.to_file` 渲染函数，按需生成并缓存到任务目录；
前端新增 URL 构造函数与词云卡片渲染源切换。

**Tech Stack:** FastAPI / Starlette FileResponse、Python `wordcloud`、Vue 3 + ECharts、Node `node:test`、pytest。

## Global Constraints

- 不新增 npm / pip 依赖（`wordcloud`、`matplotlib` 已在 requirements.txt）。
- 后端业务层不 import FastAPI；HTTP 语义只在 `src/inference/app.py`。
- 对外绝不接受路径 / 任意文件名；`stage`、`tone` 必须白名单。
- CSV 统一 UTF-8 BOM，Excel 可直接打开；下载响应 `Cache-Control: no-store`。
- 现有测试全绿是硬门槛：`python -m pytest tests/ -q`、`cd frontend && npm test`、`npm run build`。
- 所有中文注释/文案与现有代码风格一致；不改既有契约字段语义。

## 文件地图

| 文件 | 责任 |
| --- | --- |
| `src/videos/placeholder.py` | 词云每侧词量常量 |
| `src/artifacts/pipeline.py` | 数据项目词量常量 |
| `src/videos/exporting.py`（新） | 阶段 JSON → CSV 字节 |
| `src/videos/service.py` | `export_csv()` / `wordcloud_image()` |
| `src/videos/errors.py` | 新增 400/404 业务异常 |
| `src/artifacts/service.py` | `download_csv()` / `wordcloud_image()` |
| `src/visualization/wordclouds.py` | `render_frequencies_to_png()` |
| `src/inference/downloads.py`（新） | `Content-Disposition` 构造 |
| `src/inference/app.py` | 5 个新路由 |
| `frontend/src/utils/exportFiles.js`（新） | URL / 版本号 / 触发下载 |
| `frontend/src/components/charts/WordCloud.vue` | 渲染源切换 + 自适应字号 |
| `frontend/src/components/ProjectDetail.vue` / `VideoDetail.vue` | 导出按钮 + 传 imageUrl |
| `frontend/src/api/artifacts.js` / `videos.js` | 新 URL 构造导出 |
| `frontend/mock/*` | mock 模式下载/图片兜底 |
| `tests/test_export_download_stages.py`（新） | 导出端点测试 |
| `tests/test_wordcloud_image.py`（新） | 图片端点 + 词量测试 |

---

### Task 1: 词量常量 8/50 → 60

**Files:**
- Modify: `src/videos/placeholder.py:67`（`KEYWORDS_PER_SIDE = 8`）
- Modify: `src/artifacts/pipeline.py:58`（`KEYWORD_LIMIT = 50`）
- Test: `tests/test_wordcloud_image.py`（新文件，先只放词量用例）

**Interfaces:**
- Produces: `src.videos.placeholder.KEYWORDS_PER_SIDE == 60`、`src.artifacts.pipeline.KEYWORD_LIMIT == 60`。

- [ ] **Step 1: 写失败测试** `tests/test_wordcloud_image.py`

```python
"""词云词量与 Python 渲染词云图端点。"""
from __future__ import annotations


def test_keyword_limits_are_sixty():
    from src.artifacts import pipeline
    from src.videos import placeholder

    assert placeholder.KEYWORDS_PER_SIDE == 60
    assert pipeline.KEYWORD_LIMIT == 60


def test_video_real_keywords_return_sixty_when_enough_words():
    from src.videos.placeholder import _keywords_from_texts

    words = [f"词{i}号" for i in range(80)]
    text = " ".join(words * 2)
    picked = _keywords_from_texts([text])
    assert len(picked) == 60
    assert picked[0]["count"] >= picked[-1]["count"]
```

- [ ] **Step 2: 跑测试确认失败**：`python -m pytest tests/test_wordcloud_image.py -q` → 2 failed。
- [ ] **Step 3: 改两个常量**（顺带更新注释为「每侧最多取词数」）。
- [ ] **Step 4: 跑测试通过**：`python -m pytest tests/test_wordcloud_image.py -q` → 2 passed。
- [ ] **Step 5: 回归既有词云用例**：`python -m pytest tests/test_visualization.py tests/test_videos_real_crawl.py -q`。
- [ ] **Step 6: Commit**：`git add src/videos/placeholder.py src/artifacts/pipeline.py tests/test_wordcloud_image.py && git commit -m "feat(keywords): 词云每侧词量提升到 60"`

---

### Task 2: 数据项目导出 + 视频任务导出端点

**Files:**
- Create: `src/inference/downloads.py`
- Create: `src/videos/exporting.py`
- Modify: `src/videos/errors.py`（新增 `InvalidExportStageError`、`VideoDataUnavailableError`）
- Modify: `src/videos/service.py`（新增 `export_csv`）
- Modify: `src/artifacts/service.py`（新增 `download_csv`；`_clear_run_artifacts` 加词云 PNG）
- Modify: `src/inference/app.py`（两个下载路由 + 异常映射）
- Test: `tests/test_export_download_stages.py`（新）

**Interfaces:**
- Produces:
  - `src.inference.downloads.attachment_headers(filename: str) -> dict[str, str]`
  - `src.videos.exporting.STAGE_COLUMNS: dict[str, tuple[str, ...]]`（`crawl` / `clean`）
  - `src.videos.exporting.stage_csv(bvid: str, stage: str, payload: dict) -> tuple[bytes, str]`
  - `src.videos.service.VideoService.export_csv(bvid: str, stage: str = "crawl") -> dict`（`content` / `filename`）
  - `src.artifacts.service.ArtifactService.download_csv(project_id: str, stage: str = "clean") -> dict`（`content` / `filename`）
  - HTTP：`GET /api/artifacts/{id}/download?stage=`、`GET /api/videos/{bvid}/download?stage=`

**CSV 列（契约锁定）**

- `crawl`：`bvid,aid,video_title,rpid,level,root_rpid,parent_rpid,text,likes,reply_count,ctime_utc,collected_at_utc`
  （直接复用 `src.videos.crawler_adapter.STANDARD_FIELDS`）
- `clean`：crawl 全部列 + `raw_text`

- [ ] **Step 1: 写失败测试** `tests/test_export_download_stages.py`

```python
"""导出端点：数据项目三阶段 CSV、视频任务 crawl/clean CSV。"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from src.inference.app import app
from src.videos import service as video_service
from src.videos import store as video_store


@pytest.fixture
def client():
    return TestClient(app)


def _write_stage(store: video_store.VideoStore, bvid: str, stage: str, records: list[dict]) -> None:
    store.write_stage(bvid, stage, {"bvid": bvid, "mode": "real", "comment_total": len(records),
                                    "records": records})


@pytest.fixture
def video_task(tmp_path):
    store = video_store.VideoStore(root=tmp_path / "videos")
    bvid = "BV1xx411c7mD"
    store.upsert_task({"bvid": bvid, "title": "t", "status": "completed", "progress": 100,
                       "message": "分析完成", "comment_count": 2, "created_at": "2026-11-04T10:00:00+08:00",
                       "updated_at": "2026-11-04T10:00:00+08:00", "run_id": 1, "seq": 1})
    record = {"bvid": bvid, "aid": "1", "video_title": "标题", "rpid": "9", "level": 1,
              "root_rpid": "", "parent_rpid": "", "text": "清洗后文本", "likes": 3,
              "reply_count": 1, "ctime_utc": "2026-11-01T00:00:00+00:00",
              "collected_at_utc": "2026-11-02T00:00:00+00:00"}
    _write_stage(store, bvid, "crawl", [record])
    _write_stage(store, bvid, "clean", [{**record, "raw_text": "清洗前原文"}])
    service = video_service.VideoService(root=tmp_path / "videos")
    video_service.set_service(service)
    yield bvid
    service.shutdown()
    video_service.set_service(None)


def _read_csv(response) -> tuple[list[str], list[dict]]:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    return text.splitlines()[0].split(","), rows


def test_video_crawl_export_has_crawler_columns(client, video_task):
    header, rows = _read_csv(client.get(f"/api/videos/{video_task}/download?stage=crawl"))
    assert header == ["bvid", "aid", "video_title", "rpid", "level", "root_rpid",
                      "parent_rpid", "text", "likes", "reply_count", "ctime_utc", "collected_at_utc"]
    assert rows[0]["text"] == "清洗后文本"


def test_video_clean_export_appends_raw_text(client, video_task):
    header, rows = _read_csv(client.get(f"/api/videos/{video_task}/download?stage=clean"))
    assert header[-1] == "raw_text"
    assert rows[0]["raw_text"] == "清洗前原文"


def test_video_export_rejects_bad_stage_and_unknown_task(client, video_task):
    assert client.get(f"/api/videos/{video_task}/download?stage=analyze").status_code == 400
    assert client.get("/api/videos/BV1xxxxxxxxx/download?stage=crawl").status_code == 404


def test_artifact_export_returns_stage_csv(client, artifacts_client):
    project = artifacts_client.post(
        "/api/artifacts/upload",
        files={"files": ("demo.csv", b"text\n这个商品很好\n质量太差\n".encode("utf-8-sig"), "text/csv")},
    ).json()
    # 等处理完成（artifacts_client fixture 会把节奏调快，见 tests/test_artifacts_api.py）
    response = artifacts_client.get(f"/api/artifacts/{project['id']}/download?stage=initial")
    header, rows = _read_csv(response)
    assert "text" in header and rows
    assert artifacts_client.get(f"/api/artifacts/{project['id']}/download?stage=bad").status_code == 400
    assert artifacts_client.get("/api/artifacts/p_20260101_0000_zzzzzz/download").status_code == 404
```

> `artifacts_client` fixture 的现成写法见 `tests/test_artifacts_api.py`（含自动处理完成等待工具），
> 直接复用其 fixture / helper；如没有可直接用的，参考该文件构造 `TestClient` 与 `ARTIFACTS_DIR`。

- [ ] **Step 2: 跑测试确认失败**：`python -m pytest tests/test_export_download_stages.py -q` → 全部 404/ImportError。
- [ ] **Step 3: 实现 `src/inference/downloads.py`**

```python
"""下载响应的 HTTP 头构造（RFC 5987，中文文件名安全）。"""
from __future__ import annotations

from urllib.parse import quote


def attachment_headers(filename: str) -> dict[str, str]:
    """返回附件下载头：ASCII 兜底名 + UTF-8 filename*，并禁止缓存与嗅探。"""
    fallback = "".join(ch for ch in filename if ch.isascii() and ch not in '\\/:*?"<>|\r\n') or "download.csv"
    return {
        "Content-Disposition": (
            f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename)}'
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
```

- [ ] **Step 4: 实现 `src/videos/exporting.py`**

```python
"""视频任务阶段产物（crawl.json / clean.json）→ CSV 字节。"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping

from .crawler_adapter import STANDARD_FIELDS

STAGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "crawl": tuple(STANDARD_FIELDS),
    "clean": (*STANDARD_FIELDS, "raw_text"),
}
FILENAME_PREFIX = {"crawl": "collect", "clean": "clean"}


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def stage_csv(bvid: str, stage: str, payload: Mapping) -> tuple[bytes, str]:
    """把阶段产物 records[] 写成 UTF-8 BOM CSV；返回 (字节, 文件名)。"""
    columns = STAGE_COLUMNS[stage]
    records = payload.get("records") if isinstance(payload, Mapping) else None
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for record in records or []:
        if isinstance(record, Mapping):
            writer.writerow({name: _cell(record.get(name)) for name in columns})
    filename = f"{FILENAME_PREFIX[stage]}_{bvid}.csv"
    return buffer.getvalue().encode("utf-8-sig"), filename
```

- [ ] **Step 5: 新增异常 + 服务方法**

`src/videos/errors.py`:

```python
class InvalidExportStageError(VideoError):
    """导出的阶段不是 crawl / clean（HTTP 400）。"""


class VideoDataUnavailableError(VideoError):
    """任务存在但所需产物尚未生成（导出 / 词云图，HTTP 404）。"""
```

`src/videos/service.py`（`delete` 之前插入，import 同步补上）：

```python
    def export_csv(self, bvid: str, stage: str = "crawl") -> dict[str, Any]:
        """导出采集 / 清洗阶段 CSV；任务不存在或产物未生成 → 对应业务异常。"""
        from .exporting import STAGE_COLUMNS, stage_csv

        if self.store.get_task(bvid) is None:
            raise VideoNotFoundError(f"任务不存在：{bvid}")
        if stage not in STAGE_COLUMNS:
            raise InvalidExportStageError("导出阶段只支持 crawl / clean")
        payload = self.store.read_stage(bvid, stage)
        if not payload:
            raise VideoDataUnavailableError("该阶段产物尚未生成，暂无可导出的数据")
        content, filename = stage_csv(bvid, stage, payload)
        return {"content": content, "filename": filename}
```

`src/artifacts/service.py`（`open_csv` 前插入）：

```python
    def download_csv(self, project_id: str, stage: str = "clean") -> dict[str, Any]:
        """导出某阶段 CSV（浏览器下载）；项目不存在 / 文件未生成 → 404。"""
        record = self.store.require_project(project_id)
        path = self.store.resolve_open_path(project_id, stage)  # stage 白名单 + 根目录校验
        if not path.is_file():
            raise ArtifactNotFoundError("该阶段文件尚未生成，暂无可导出的数据")
        name = str(record.get("name") or project_id)
        safe_name = "".join(ch for ch in name if ch not in '\\/:*?"<>|\r\n').strip() or project_id
        return {"content": path.read_bytes(), "filename": f"{safe_name}_{stage}.csv"}
```

- [ ] **Step 6: 路由**（`src/inference/app.py`，紧跟既有 `path` 路由后；import 补
  `InvalidExportStageError, VideoDataUnavailableError` 与 `from .downloads import attachment_headers`）

```python
@app.get("/api/artifacts/{project_id}/download", summary="下载某阶段 CSV（浏览器导出）", tags=["数据项目"])
def download_artifact(project_id: str, stage: str = Query("clean", description="initial | clean | excluded")) -> Response:
    try:
        payload = artifacts_service.get_service().download_csv(project_id, stage)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=payload["content"], media_type="text/csv; charset=utf-8",
                    headers=attachment_headers(payload["filename"]))


@app.get("/api/videos/{bvid}/download", summary="导出采集 / 清洗数据 CSV", tags=["视频分析"])
def download_video_task(bvid: str, stage: str = Query("crawl", description="crawl | clean")) -> Response:
    try:
        payload = videos_service.get_service().export_csv(bvid, stage)
    except (VideoNotFoundError, VideoDataUnavailableError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidExportStageError, InvalidBvidError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=payload["content"], media_type="text/csv; charset=utf-8",
                    headers=attachment_headers(payload["filename"]))
```

- [ ] **Step 7: 跑测试通过** + `python -m pytest tests/test_artifacts_api.py tests/test_videos_api.py -q` 回归。
- [ ] **Step 8: Commit**：`git add src tests && git commit -m "feat(export): 数据项目与视频任务导出 CSV"`

---

### Task 3: Python 渲染词云图端点

**Files:**
- Modify: `src/visualization/wordclouds.py`（新增 `WordcloudDependencyError` 与 `render_frequencies_to_png`）
- Modify: `src/videos/service.py`（`wordcloud_image`）
- Modify: `src/artifacts/service.py`（`wordcloud_image`、`_clear_run_artifacts`）
- Modify: `src/inference/app.py`（两个图片路由）
- Test: `tests/test_wordcloud_image.py`（追加）

**Interfaces:**
- Produces:
  - `src.visualization.wordclouds.render_frequencies_to_png(frequencies, out_path, *, tone="positive", width=960, height=600) -> Path`
  - `VideoService.wordcloud_image(bvid, tone="positive") -> Path`
  - `ArtifactService.wordcloud_image(project_id, tone="positive") -> Path`
  - `GET /api/videos/{bvid}/wordcloud-image?tone=&download=`、
    `GET /api/artifacts/{id}/wordcloud-image?tone=&download=`

- [ ] **Step 1: 追加失败测试**（`tests/test_wordcloud_image.py`）

```python
@pytest.mark.skipif(importlib.util.find_spec("wordcloud") is None, reason="wordcloud 未安装")
def test_render_frequencies_to_png(tmp_path):
    from src.visualization.wordclouds import render_frequencies_to_png

    path = render_frequencies_to_png({"讲解": 20, "清晰": 12, "实用": 8}, tmp_path / "a.png", tone="positive")
    assert path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "a.tmp.png").exists() is False


def test_video_wordcloud_image_uses_cache(client, video_task):
    store = video_service.get_service().store
    store.write_detail(video_task, {"keywords": {"positive": [
        {"word": "讲解", "count": 10, "weight": 1.0}, {"word": "清晰", "count": 6, "weight": 0.6},
        {"word": "实用", "count": 3, "weight": 0.3}], "negative": []}})
    first = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert first.status_code == 200 and first.headers["content-type"] == "image/png"
    mtime = store.stage_dir(video_task).joinpath("wordcloud_positive.png").stat().st_mtime_ns
    second = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert second.content == first.content
    assert store.stage_dir(video_task).joinpath("wordcloud_positive.png").stat().st_mtime_ns == mtime


def test_wordcloud_image_validation(client, video_task):
    assert client.get(f"/api/videos/{video_task}/wordcloud-image?tone=neutral").status_code == 400
    assert client.get("/api/videos/BV1xxxxxxxxx/wordcloud-image").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现渲染函数**（`src/visualization/wordclouds.py` 末尾追加；无 matplotlib 依赖）

```python
class WordcloudDependencyError(ImportError):
    """wordcloud 库缺失（HTTP 503，detail 带安装指引）。"""


COLORMAPS = {"positive": "Greens", "negative": "Reds"}


def render_frequencies_to_png(
    frequencies: Mapping[str, int],
    out_path: str | Path,
    *,
    tone: str = "positive",
    width: int = 960,
    height: int = 600,
) -> Path:
    """用 wordcloud 库把 ``{词: 频次}`` 渲染成白底 PNG（原子写盘，返回路径）。

    正面用绿色系、负面用红色系；CJK 字体缺失时仍能出图（文字为方块，调用方记录 warning）。
    """
    cleaned = {str(word): int(count) for word, count in frequencies.items()
               if is_display_keyword(str(word)) and int(count) > 0}
    if len(cleaned) < 2:
        raise ValueError("可绘制的有效词不足 2 个")
    try:
        from wordcloud import WordCloud  # noqa: PLC0415 - 函数内导入
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise WordcloudDependencyError(
            "Python 词云渲染需要 wordcloud，请执行：python -m pip install wordcloud"
        ) from exc
    font = detect_cjk_font()
    if font is None:
        logging.getLogger(__name__).warning("未找到中文字体，Python 词云可能显示方块；可设置 WORDCLOUD_FONT")
    cloud = WordCloud(
        font_path=font, width=width, height=height, background_color="white",
        colormap=COLORMAPS.get(tone, "Greens"), random_state=42, collocations=False,
        max_words=200, prefer_horizontal=1.0, margin=4,
    )
    cloud.generate_from_frequencies(cleaned)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    try:
        cloud.to_file(str(tmp))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path
```

- [ ] **Step 4: 服务方法**

`src/videos/service.py`（`export_csv` 后）：

```python
    def wordcloud_image(self, bvid: str, tone: str = "positive") -> Path:
        """按需渲染词频词云 PNG（缓存到 ``<root>/<bvid>/wordcloud_<tone>.png``）。"""
        from ..visualization import wordclouds

        if tone not in ("positive", "negative"):
            raise InvalidExportStageError("tone 只支持 positive / negative")
        task = self.store.get_task(bvid)
        if task is None:
            raise VideoNotFoundError(f"任务不存在：{bvid}")
        stored = self.store.read_detail(bvid) or {}
        words = clean_keyword_groups(stored.get("keywords"), precision=3).get(tone) or []
        frequencies = {item["word"]: item["count"] for item in words}
        if len(frequencies) < 2:
            raise VideoDataUnavailableError("该情感暂无足够关键词生成词云")
        target = self.store.stage_dir(bvid) / f"wordcloud_{tone}.png"
        source = self.store.detail_path(bvid)
        if target.is_file() and source.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return target
        return wordclouds.render_frequencies_to_png(frequencies, target, tone=tone)
```

`src/artifacts/service.py`（`download_csv` 后；`_clear_run_artifacts` 的元组追加两 PNG）：

```python
    def wordcloud_image(self, project_id: str, tone: str = "positive") -> Path:
        """按需渲染数据项目词云 PNG（缓存到项目目录）。"""
        from ..visualization import wordclouds

        if tone not in ("positive", "negative"):
            raise ArtifactValidationError("tone 只支持 positive / negative")
        self.store.require_project(project_id)
        analysis_path = self.store.artifact_path(project_id, "analysis.json")
        try:
            payload = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            payload = {}
        words = clean_keyword_groups(payload.get("keywords")).get(tone) or []
        frequencies = {item["word"]: item["count"] for item in words}
        if len(frequencies) < 2:
            raise ArtifactNotFoundError("该情感暂无足够关键词生成词云")
        target = self.store.artifact_path(project_id, f"wordcloud_{tone}.png")
        if target.is_file() and target.stat().st_mtime_ns >= analysis_path.stat().st_mtime_ns:
            return target
        return wordclouds.render_frequencies_to_png(frequencies, target, tone=tone)
```

- [ ] **Step 5: 路由**（`src/inference/app.py`；`FileResponse` 已导入）

```python
@app.get("/api/videos/{bvid}/wordcloud-image", summary="Python 渲染的词云图", tags=["视频分析"])
def get_video_wordcloud_image(bvid: str, tone: str = Query("positive"), download: bool = False) -> FileResponse:
    try:
        path = videos_service.get_service().wordcloud_image(bvid, tone)
    except (VideoNotFoundError, VideoDataUnavailableError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExportStageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportError as exc:  # WordcloudDependencyError
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=path.name,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@app.get("/api/artifacts/{project_id}/wordcloud-image", summary="Python 渲染的数据项目词云图", tags=["数据项目"])
def get_artifact_wordcloud_image(project_id: str, tone: str = Query("positive"), download: bool = False) -> FileResponse:
    try:
        path = artifacts_service.get_service().wordcloud_image(project_id, tone)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", filename=path.name,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
```

- [ ] **Step 6: 跑测试通过** + 全量后端回归。
- [ ] **Step 7: Commit**：`git add src tests && git commit -m "feat(wordcloud): Python 渲染词云图端点与缓存"`

---

### Task 4: 前端导出按钮

**Files:**
- Create: `frontend/src/utils/exportFiles.js`
- Modify: `frontend/src/api/artifacts.js`、`frontend/src/api/videos.js`（转发 URL 构造）
- Modify: `frontend/src/components/ProjectDetail.vue`（底部三导出按钮）
- Modify: `frontend/src/components/VideoDetail.vue`（完成态 + 等待态导出按钮）
- Modify: `frontend/mock/artifacts.js`、`frontend/mock/store.js`（下载兜底）
- Test: `frontend/tests/export-files.test.js`

**Interfaces:**
- Produces（`exportFiles.js`）：
  - `artifactDownloadUrl(projectId, stage = 'clean') -> string`
  - `videoDownloadUrl(bvid, stage = 'crawl') -> string`
  - `wordcloudImageUrl({ kind, id, tone, version = '', download = false }) -> string`
  - `keywordsRevision(words) -> string`
  - `triggerDownload(url)`（DOM 副作用，组件用）

- [ ] **Step 1: 写失败测试** `frontend/tests/export-files.test.js`

```js
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  artifactDownloadUrl,
  keywordsRevision,
  videoDownloadUrl,
  wordcloudImageUrl,
} from '../src/utils/exportFiles.js'

test('artifactDownloadUrl 拼接阶段参数并编码 id', () => {
  assert.equal(artifactDownloadUrl('p_1', 'clean'), '/api/artifacts/p_1/download?stage=clean')
  assert.equal(artifactDownloadUrl('p 1', 'initial'), '/api/artifacts/p%201/download?stage=initial')
})

test('videoDownloadUrl 默认 crawl', () => {
  assert.equal(videoDownloadUrl('BV1xx411c7mD'), '/api/videos/BV1xx411c7mD/download?stage=crawl')
})

test('wordcloudImageUrl 带 tone 与版本参数', () => {
  assert.equal(
    wordcloudImageUrl({ kind: 'video', id: 'BV1xx411c7mD', tone: 'negative', version: '3-21' }),
    '/api/videos/BV1xx411c7mD/wordcloud-image?tone=negative&v=3-21',
  )
  assert.match(
    wordcloudImageUrl({ kind: 'artifact', id: 'p_1', tone: 'positive', download: true }),
    /download=true/,
  )
})

test('keywordsRevision 随词内容变化且确定', () => {
  const words = [{ word: '清晰', count: 10 }, { word: '实用', count: 4 }]
  assert.equal(keywordsRevision(words), keywordsRevision(words))
  assert.notEqual(keywordsRevision(words), keywordsRevision([{ word: '清晰', count: 9 }]))
})
```

- [ ] **Step 2: 跑 `cd frontend && npm test` 确认失败**。
- [ ] **Step 3: 实现 `exportFiles.js`**

```js
/**
 * src/utils/exportFiles.js — 导出 / Python 词云图 URL 构造（纯函数，便于 Node 单测）。
 */
export function artifactDownloadUrl(projectId, stage = 'clean') {
  return `/api/artifacts/${encodeURIComponent(projectId)}/download?stage=${encodeURIComponent(stage)}`
}

export function videoDownloadUrl(bvid, stage = 'crawl') {
  return `/api/videos/${encodeURIComponent(bvid)}/download?stage=${encodeURIComponent(stage)}`
}

export function wordcloudImageUrl({ kind, id, tone = 'positive', version = '', download = false }) {
  const base = kind === 'artifact'
    ? `/api/artifacts/${encodeURIComponent(id)}/wordcloud-image`
    : `/api/videos/${encodeURIComponent(id)}/wordcloud-image`
  const search = new URLSearchParams({ tone })
  if (version) search.set('v', version)
  if (download) search.set('download', 'true')
  return `${base}?${search.toString()}`
}

/** 由关键词内容生成确定性版本号，重新分析后浏览器不会命中旧图。 */
export function keywordsRevision(words) {
  const list = Array.isArray(words) ? words : []
  const total = list.reduce((sum, item) => sum + (Number(item?.count) || 0), 0)
  const chars = list.reduce((sum, item) => sum + String(item?.word || '').length, 0)
  return `${list.length}-${total}-${chars}`
}

/** 触发浏览器下载（不新开窗口）。 */
export function triggerDownload(url) {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}
```

- [ ] **Step 4: UI 接线**
  - `ProjectDetail.vue`：底部操作区加「导出原始爬虫 CSV / 导出清洗结果 CSV / 导出排除记录 CSV」，
    `:disabled="!isCompleted || stageRows(stage).rows === 0"`；点击 `triggerDownload(artifactDownloadUrl(projectId, stage))`；
    失败提示复用 `actionError`（或加 `exportMessage`）。
  - `VideoDetail.vue`：完成态与 `crawled` / `cleaned` 等待态加「导出采集数据 CSV」「导出清洗数据 CSV」；
    `crawled` 只显示采集导出，`cleaned` 显示两个；未完成态不显示。
  - 按钮加 `Icon name="download-2-line"`（图标已内置，见 `src/assets/icons`）。
- [ ] **Step 5: mock 兜底**：`frontend/mock/artifacts.js` / `store.js` 对新路径返回
  `text/csv` 假数据（表头 + 2 行）或 404 JSON（图片返回
  `{"detail":"mock 模式不支持 Python 词云图，请连接真实后端"}`），保证 demo 不白屏。
- [ ] **Step 6: `npm test` 通过 + `npm run build` 通过**。
- [ ] **Step 7: Commit**：`git add frontend && git commit -m "feat(frontend): 数据导出按钮与下载工具"`

---

### Task 5: 前端词云切换 + 自适应字号

**Files:**
- Modify: `frontend/src/components/charts/WordCloud.vue`
- Modify: `frontend/src/components/ProjectDetail.vue`、`VideoDetail.vue`（传 `imageUrl`）
- Test: `frontend/tests/export-files.test.js` 已覆盖 URL/版本纯函数；组件行为由浏览器验收覆盖。

**Interfaces:**
- `WordCloud.vue` 新 props：`imageUrl: String = ''`、`imageDownloadUrl: String = ''`。
  组件内部状态 `source: 'frontend' | 'python'`（默认 `frontend`）、`imageState: 'idle'|'loading'|'ready'|'error'`。
- 卡片头：`words.length 个词` + 当 `imageUrl` 非空时显示分段按钮「前端渲染 | Python 图」。
- 字号：`sizeRange = words.length > 15 ? [10, 30] : [13, 40]`；`gridSize: 5`；卡片高度 320px。
- Python 模式：`<img :src="imageUrl" loading="lazy" @load @error>`，
  错误时显示「Python 渲染失败：<原因>」并自动切回 frontend；下载链接用 `imageDownloadUrl`。

- [ ] **Step 1: 改 `WordCloud.vue`**：按上述 props/状态实现，注意 `computed` 的 `option` 不变，
  仅在模板中条件渲染 `<EChart>` 或 `<img>`。
- [ ] **Step 2: 传参**：
  - `VideoDetail.vue`：`const bvid = computed(...)`；每张词云
    `:image-url="wordcloudImageUrl({ kind:'video', id: bvid, tone:'positive', version: keywordsRevision(keywords.positive) })"`，
    `:image-download-url` 同上 `download: true`。
  - `ProjectDetail.vue`：同样用 `analysis.keywords[tone]` 与 `project.id`（`kind:'artifact'`）。
- [ ] **Step 3: `npm test`、`npm run build` 通过**。
- [ ] **Step 4: Commit**：`git add frontend && git commit -m "feat(frontend): 词云支持切换 Python 渲染图并自适应字号"`

---

### Task 6: 文档 + 集成验收（Lead）

**Files:**
- Modify: `docs/API契约.md`（新增 5 个端点、词量 60、前端切换说明）
- Modify: `README.md`（功能表、接口一览、词云说明）
- Modify: `frontend/README.md`（新 props / 组件行为）
- Modify: `frontend/mock/README`（如有）

- [ ] **Step 1: 后端全量**：`python -m pytest tests/ -q`（预期全绿，无新增 skip）。
- [ ] **Step 2: 前端**：`cd frontend && npm test && npm run build`。
- [ ] **Step 3: 起真实后端**：`python -m uvicorn src.inference.app:app --host 127.0.0.1 --port 8000`（后台任务）。
- [ ] **Step 4: 起前端**：`cd frontend && npm run dev -- --host 127.0.0.1 --port 3000`（后台任务）。
- [ ] **Step 5: 浏览器验收**（截图留证）：
  - 上传 `data/sample/bilibili_demo.csv` → 项目完成 → 三个导出按钮下载文件，`wc -l` 与页面行数一致；
  - 已完成视频任务 `BV16BjHz9EUJ`：词云每侧 60 词、切 Python 图正常显示、下载 PNG；
  - 视频导出 CSV 行数 2864（crawl）/ 2792（clean）；
  - 后端停掉时导出提示错误不白屏。
- [ ] **Step 6: 更新文档**并提交：`git add docs README.md frontend/README.md && git commit -m "docs: 导出与词云功能契约与说明"`。
- [ ] **Step 7: 最终 `git status` 干净，`git log --oneline -6` 展示提交。
