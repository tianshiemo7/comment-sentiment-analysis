#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/verify_frontend_icons.py — 前端图标接入的自动化验收（Playwright + headless Chromium）

对应用户可见的行为验收，`frontend/README.md`「六·五、美术资源与许可」一节引用的 24 项断言，
就是本脚本跑出来的。**不属于 CI**：CI 只跑后端 pytest 与前端 `npm run build`；
本脚本需要额外的可选依赖与一份浏览器，属于本地/发版前的人工验收工具。

依赖（都不在 requirements.txt 里，按需自行安装）：

    pip install playwright
    playwright install chromium        # 或复用已有的浏览器（见下）

若浏览器装在非默认位置（例如本机是 ms-playwright 缓存），用环境变量指过去：

    PLAYWRIGHT_BROWSERS_PATH="C:/Users/<你>/AppData/Local/ms-playwright" \\
        python scripts/verify_frontend_icons.py

前置：先把前端 dev server 跑起来（自带 mock，**不需要**后端）

    cd frontend && npm run dev          # http://localhost:3000

用法：

    python scripts/verify_frontend_icons.py                       # 默认打 http://localhost:3000
    python scripts/verify_frontend_icons.py http://localhost:3000
    python scripts/verify_frontend_icons.py --out ./shots         # 指定截图目录

产物：截图 + report.txt（UTF-8）。退出码 0 表示全部通过。

覆盖的 24 项断言：
  1  导航 5 个入口                    2  导航每项都有图标
  3  主界面图标已渲染                 4  没有空 path 的图标
  5  数据项目详情渲染图标             6  展开逐条评论后图标仍在
  7  ②段详情 canvas = 0               8  单独评论详情渲染图标
  9  视频详情渲染图标                10  视频详情图表 canvas > 0
  11 上传弹窗渲染图标                12  整栏收起后箭头图标仍在
  13 左栏三段都有收起箭头+段图标     14  纯图标按钮都有 aria-label + title
  15-18 其余四个视图都渲染图标        19  模型对比页对未接入后端给出中文提示
  20 减少动效时旋转动画被禁用        21  没有「未知图标名」告警
  22 除未接入的 /api/models 外没有 4xx/5xx   23 除已知代理失败外无控制台错误
  24 0 个未捕获异常
"""
import argparse
import json
import os
import sys
import tempfile

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit(
        "缺少 playwright，请先安装：\n"
        "    pip install playwright\n"
        "    playwright install chromium"
    )

# 契约里 /api/models 不在 mock 覆盖范围内，后端未启动时经 Vite 代理必然 500。
# 这是既有设计（页面会显示「待接入后端」），不是回归，因此不计入失败。
KNOWN_UNMOCKED = "/api/models"

RESULTS = []
LOG_LINES = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    line = ("PASS" if ok else "FAIL") + " | " + name + ((" | " + str(detail)) if detail else "")
    LOG_LINES.append(line)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # Windows 控制台可能是 GBK
        print(line.encode("utf-8", "replace").decode("ascii", "replace"), flush=True)


def icon_stats(page):
    """返回页面内 Icon.vue 渲染出的 svg 统计：(总数, 可见数, 空 path 数)"""
    return page.evaluate(
        """() => {
            const svgs = [...document.querySelectorAll('svg.icon')];
            const visible = svgs.filter(s => s.getBoundingClientRect().width > 0).length;
            const empty = svgs.filter(s => {
                const p = s.querySelector('path');
                return !p || !p.getAttribute('d');
            }).length;
            return { icons: svgs.length, visible, empty };
        }"""
    )


def run(base_url, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    def shot(page, name):
        page.screenshot(path=os.path.join(out_dir, name + ".png"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 950}, locale="zh-CN")
        page = ctx.new_page()

        console_errors, console_warns, page_errors, bad_responses = [], [], [], []
        page.on(
            "console",
            lambda m: (
                console_errors.append(m.text)
                if m.type == "error"
                else (console_warns.append(m.text) if m.type == "warning" else None)
            ),
        )
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("response", lambda r: bad_responses.append((r.status, r.url)) if r.status >= 400 else None)

        page.goto(base_url, wait_until="networkidle")
        page.wait_for_timeout(800)

        # ---------- 顶部导航 ----------
        nav = page.locator(".nav__item")
        check("导航 5 个入口", nav.count() == 5, "实际 %d" % nav.count())
        check("导航每项都有图标", page.locator(".nav__item svg.icon").count() == 5)

        # ---------- 主界面默认空态 ----------
        st = icon_stats(page)
        check("主界面图标已渲染", st["visible"] > 0, json.dumps(st, ensure_ascii=False))
        check("没有空 path 的图标", st["empty"] == 0, "empty=%d" % st["empty"])
        shot(page, "01-bilibili-empty")

        # ---------- 数据项目详情（选已完成的那条，处理中的不渲染三张表） ----------
        done_p = page.locator(".project-item--completed").first
        if done_p.count() == 0:
            check("存在已完成的数据项目（mock 预置）", False, "找不到 .project-item--completed")
        else:
            done_p.click()
            page.wait_for_timeout(1500)
            st = icon_stats(page)
            check("数据项目详情渲染图标", st["visible"] > 0, json.dumps(st, ensure_ascii=False))
            shot(page, "02-project-detail")
            page.locator(".artifact__expand").click()
            page.wait_for_timeout(1000)
            shot(page, "03-project-comments-open")
            st = icon_stats(page)
            check("展开逐条评论后图标仍在", st["visible"] > 0, json.dumps(st, ensure_ascii=False))

        # ---------- 单独评论详情（契约：不渲染图表 → canvas = 0） ----------
        page.locator(".solo-item").first.click()
        page.wait_for_timeout(700)
        canvas = page.locator("canvas").count()
        check("②段详情 canvas = 0", canvas == 0, "canvas=%d" % canvas)
        st = icon_stats(page)
        check("单独评论详情渲染图标", st["visible"] > 0, json.dumps(st, ensure_ascii=False))
        shot(page, "04-solo-detail")

        # ---------- 视频任务详情 ----------
        done_v = page.locator(".video-item--completed").first
        if done_v.count() == 0:
            check("存在已完成的视频任务（mock 预置）", False, "找不到 .video-item--completed")
        else:
            done_v.click()
            page.wait_for_timeout(1800)
            shot(page, "05-video-detail")
            st = icon_stats(page)
            check("视频详情渲染图标", st["visible"] > 0, json.dumps(st, ensure_ascii=False))
            charts = page.locator("canvas").count()
            check("视频详情图表 canvas > 0", charts > 0, "canvas=%d" % charts)

        # ---------- 上传弹窗 ----------
        page.get_by_role("button", name="上传爬取数据").click()
        page.wait_for_timeout(600)
        st = icon_stats(page)
        check("上传弹窗渲染图标", st["visible"] > 0, json.dumps(st, ensure_ascii=False))
        shot(page, "06-upload-dialog")
        page.locator(".modal__close").click()
        page.wait_for_timeout(400)

        # ---------- 左栏收起 / 三段收起 ----------
        page.locator(".sidebar__toggle").click()
        page.wait_for_timeout(600)
        shot(page, "07-sidebar-collapsed")
        check("整栏收起后箭头图标仍在", page.locator(".sidebar__toggle svg.icon").count() == 1)
        page.locator(".rail-dot").first.click()
        page.wait_for_timeout(700)
        shot(page, "08-sidebar-collapsed-detail")
        page.locator(".sidebar__toggle").click()
        page.wait_for_timeout(600)

        carets = page.locator(".sec__caret").count()
        sec_icons = page.locator(".sec__icon").count()
        check("左栏三段都有收起箭头+段图标", carets == 3 and sec_icons == 3,
              "caret=%d icon=%d" % (carets, sec_icons))
        page.locator(".sec__toggle").first.click()
        page.wait_for_timeout(400)
        shot(page, "09-section-collapsed")
        page.locator(".sec__toggle").first.click()
        page.wait_for_timeout(400)

        # ---------- 纯图标按钮的无障碍名称 ----------
        problems = page.evaluate(
            """() => {
                const bad = [];
                for (const b of document.querySelectorAll('button.icon-btn')) {
                    const label = (b.getAttribute('aria-label') || '').trim();
                    const title = (b.getAttribute('title') || '').trim();
                    const text = (b.textContent || '').trim();
                    if (!text && (!label || !title)) bad.push((b.className || '') + ' :: ' + b.outerHTML.slice(0, 90));
                }
                return bad;
            }"""
        )
        check("纯图标按钮都有 aria-label + title", len(problems) == 0, " | ".join(problems[:4]))

        # ---------- 其余四个视图 ----------
        for key, label in [("single", "单条预测"), ("batch", "批量预测"),
                           ("visualization", "实验可视化"), ("models", "模型对比")]:
            page.get_by_role("button", name=label, exact=True).click()
            page.wait_for_timeout(900)
            st = icon_stats(page)
            check("%s 页渲染图标" % label, st["visible"] > 0, json.dumps(st, ensure_ascii=False))
            shot(page, "10-view-%s" % key)
            if key == "models":
                hint = page.locator(".hint").first.inner_text()
                check("模型对比页对未接入后端给出中文提示", "待接入后端" in hint, hint.strip()[:60])

        # ---------- prefers-reduced-motion ----------
        page.emulate_media(reduced_motion="reduce")
        page.get_by_role("button", name="B站分析", exact=True).click()
        page.wait_for_timeout(700)
        spinning = page.evaluate(
            """() => [...document.querySelectorAll('.icon--spin')].map(el => getComputedStyle(el).animationName)"""
        )
        check("减少动效时旋转动画被禁用", all(n == "none" for n in spinning), "animationName=%s" % spinning)
        page.emulate_media(reduced_motion="no-preference")

        # ---------- 控制台与网络 ----------
        unknown = [w for w in console_warns if "未知图标名" in w]
        check("没有「未知图标名」告警", len(unknown) == 0, " | ".join(unknown[:3]))

        unexpected_bad = [(s, u) for s, u in bad_responses if not u.rstrip("/").endswith(KNOWN_UNMOCKED)]
        check("除未接入的 %s 外没有 4xx/5xx 响应" % KNOWN_UNMOCKED, len(unexpected_bad) == 0,
              " | ".join("%s %s" % (s, u) for s, u in unexpected_bad[:5]))

        unexpected_console = [e for e in console_errors if "Failed to load resource" not in e]
        check("除已知 %s 代理失败外无控制台错误" % KNOWN_UNMOCKED, len(unexpected_console) == 0,
              " | ".join(unexpected_console[:3]))
        check("0 个未捕获异常", len(page_errors) == 0, " | ".join(page_errors[:3]))

        browser.close()


def main():
    parser = argparse.ArgumentParser(description="前端图标接入的自动化验收")
    parser.add_argument("base_url", nargs="?", default="http://localhost:3000",
                        help="前端地址，默认 http://localhost:3000")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "csa-verify-icons"),
                        help="截图与报告输出目录（默认系统临时目录，避免污染仓库）")
    args = parser.parse_args()

    run(args.base_url, args.out)

    failed = [r for r in RESULTS if not r[1]]
    LOG_LINES.append("")
    LOG_LINES.append("==== 汇总 ====")
    LOG_LINES.append("通过 %d/%d" % (len(RESULTS) - len(failed), len(RESULTS)))
    for name, _, detail in failed:
        LOG_LINES.append("FAILED: %s %s" % (name, detail))

    report = os.path.join(args.out, "report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LOG_LINES) + "\n")

    print("\n通过 %d/%d" % (len(RESULTS) - len(failed), len(RESULTS)), flush=True)
    print("报告与截图：%s" % args.out, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
