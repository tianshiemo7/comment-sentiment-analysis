#!/usr/bin/env node
/**
 * scripts/gen_icons.mjs — 从 Remix Icon 官方包抽取精选图标的 path 数据，
 * 生成 frontend/src/assets/icons/icons.js，并校验代码里用到的图标名都存在。
 *
 * 为什么只抽 path 而不装依赖：
 *   Remix Icon 的 *-line 图标都是「单条 <path> 的纯填充路径」，不含 circle/rect/g 等元素。
 *   因此只保留 d 字符串即可，配合 frontend/src/components/Icon.vue 渲染，
 *   无需 npm 依赖、无需图标字体、不会产生 FOUT（详见 docs/superpowers/specs/
 *   2026-10-16-frontend-remixicon-assets-design.md）。
 *
 * 用法：
 *   # 1) 取官方包（不装进 package.json，只用于抽取）
 *   cd /tmp && npm pack remixicon@4.9.1 && tar xzf remixicon-4.9.1.tgz
 *
 *   # 2) 生成 + 校验（在仓库根目录执行）
 *   node scripts/gen_icons.mjs /tmp/package
 *   node scripts/gen_icons.mjs /tmp/package --check    # 只校验，不写文件
 *
 * 许可：Remix Icon License v1.0（2026-01 版）。该版第 3.3 条禁止将图标用作品牌标识，
 * 因此本项目的品牌标记与 favicon 必须保持原创。详见 frontend/src/assets/icons/NOTICE.txt。
 */
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT_FILE = join(ROOT, 'frontend/src/assets/icons/icons.js')
const SRC_DIR = join(ROOT, 'frontend/src')

/**
 * 精选图标清单：**唯一事实来源**。
 * 分组只为可读性；新增图标时加到这里再重跑本脚本即可。
 */
const GROUPS = [
  ['顶部导航（App.vue）', ['bilibili-line', 'chat-1-line', 'file-list-3-line', 'bar-chart-2-line', 'stack-line']],
  ['左栏骨架（SidebarSection / VideoSidebar）', [
    'arrow-left-s-line', 'arrow-right-s-line', 'arrow-down-s-line',
    'folder-3-line', 'chat-quote-line', 'film-line',
  ]],
  ['状态语义（VideoStatusBadge / VideoProgress）', [
    'time-line', 'download-cloud-2-line', 'loader-4-line', 'checkbox-circle-line', 'error-warning-line',
  ]],
  ['情感极性（SentimentBadge / ProjectDetail 筛选）', [
    'emotion-happy-line', 'emotion-unhappy-line', 'emotion-normal-line', 'question-line',
  ]],
  ['结果来源（MethodBadge）', ['flask-line', 'robot-2-line']],
  ['条目操作（重试 / 重新处理 / 删除 / 关闭）', ['refresh-line', 'restart-line', 'delete-bin-6-line', 'close-line']],
  ['文件与数据（详情页按钮与弹窗）', [
    'file-excel-2-line', 'file-copy-2-line', 'file-3-line',
    'database-2-line', 'upload-2-line', 'upload-cloud-2-line', 'download-2-line',
  ]],
  ['图表卡片（charts/*、ProjectDetail、ModelMetrics）', [
    'pie-chart-2-line', 'line-chart-line', 'cloud-line', 'bar-chart-horizontal-line', 'table-line',
  ]],
  ['页面控件与空态', [
    'play-line', 'play-circle-line', 'send-plane-2-line', 'eraser-line', 'heart-pulse-line',
    'cpu-line', 'image-line', 'pulse-line', 'alert-line',
  ]],
  ['评论与指标', ['thumb-up-line']],
]

/** 建索引：图标名 → SVG 文件路径（官方包结构是 icons/<分类>/<名称>.svg） */
function indexIcons(pkgDir) {
  const index = new Map()
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.name.endsWith('.svg')) index.set(entry.name.slice(0, -4), full)
    }
  }
  walk(join(pkgDir, 'icons'))
  return index
}

/** 抽取 <path d="...">；同时强校验「单条 path、无其它图形元素、viewBox 24×24」 */
function extractPath(index, name) {
  const file = index.get(name)
  if (!file) throw new Error(`官方包中没有图标「${name}」`)
  const svg = readFileSync(file, 'utf8')
  const others = svg.match(/<(circle|rect|g|ellipse|line|polygon|polyline|use)\b/g)
  if (others) throw new Error(`「${name}」含非 path 元素：${others.join(',')}，本抽取方式不适用`)
  const paths = [...svg.matchAll(/<path\b[^>]*\bd="([^"]+)"/g)].map((m) => m[1])
  if (paths.length !== 1) throw new Error(`「${name}」的 path 数为 ${paths.length}，预期 1`)
  if (!/viewBox="0 0 24 24"/.test(svg)) throw new Error(`「${name}」的 viewBox 不是 24×24`)
  return paths[0]
}

/**
 * 扫描 frontend/src 下所有 .vue，收集代码里引用到的图标名。
 *
 * 两种引用方式都要认：
 *   静态绑定 —— <Icon name="play-line" />
 *   动态绑定 —— :name="view.icon" / STATUS_ICONS 映射表里的 'loader-4-line' 字面量
 * 动态写法只看得到字符串字面量，因此这里把所有 'xxx-line' 字面量也计入引用，
 * 否则会出现「明明在用却报告未引用」的假警报。
 */
function collectUsedIcons(dir) {
  const used = new Map() // 图标名 → 出现位置
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name)
      const rel = full.slice(ROOT.length + 1)
      if (entry.isDirectory()) walk(full)
      else if (/\.(vue|js)$/.test(entry.name) && full !== OUT_FILE) {
        const text = readFileSync(full, 'utf8')
        for (const m of text.matchAll(/["']([a-z0-9-]+-line)["']/g)) {
          const name = m[1]
          // 只统计看起来像 Remix 图标名的（避免把 'some-css-line' 之类误判）。
          // 注意 (-[a-z0-9]+)* 用 * 而不是 +：play-line / time-line 这类单词图标名也要认。
          if (!/^[a-z0-9]+(-[a-z0-9]+)*-line$/.test(name)) continue
          if (!used.has(name)) used.set(name, [])
          if (!used.get(name).includes(rel)) used.get(name).push(rel)
        }
      }
    }
  }
  walk(dir)
  return used
}

function main() {
  const args = process.argv.slice(2)
  const checkOnly = args.includes('--check')
  const pkgDir = args.find((a) => !a.startsWith('--'))

  if (!pkgDir) {
    console.error('用法：node scripts/gen_icons.mjs <remixicon 包目录> [--check]')
    console.error('示例：node scripts/gen_icons.mjs /tmp/package')
    process.exit(2)
  }

  const index = indexIcons(pkgDir)
  const allNames = GROUPS.flatMap(([, names]) => names)

  // --- 自检 1：清单内不重复 ---
  const seen = new Set()
  const dupes = allNames.filter((n) => (seen.has(n) ? true : (seen.add(n), false)))
  if (dupes.length) throw new Error(`清单内有重复图标：${dupes.join(', ')}`)

  // --- 自检 2：官方包里都存在，且都是单条 path ---
  const paths = {}
  for (const [, names] of GROUPS) for (const name of names) paths[name] = extractPath(index, name)

  // --- 自检 3：代码里用到的图标名必须都在清单里（防止拼错后静默回退成占位图形） ---
  const used = collectUsedIcons(SRC_DIR)
  const missing = [...used.keys()].filter((n) => !paths[n])
  const unused = allNames.filter((n) => !used.has(n))
  if (missing.length) {
    console.error('错误：以下图标在代码中被使用，但不在清单里（会回退为占位圆环）：')
    for (const n of missing) console.error(`  - ${n}  ← ${[...new Set(used.get(n))].join(', ')}`)
    process.exit(1)
  }

  if (checkOnly) {
    console.log(`校验通过：${allNames.length} 个图标全部存在，代码中用到的 ${used.size} 个图标名均在清单内。`)
    if (unused.length) console.log(`提示：清单中有 ${unused.length} 个图标暂未被引用 → ${unused.join(', ')}`)
    return
  }

  const lines = [
    '/**',
    ' * src/assets/icons/icons.js — Remix Icon 精选子集（仅 path 数据，无任何依赖）',
    ' *',
    ' * ⚠ 本文件由 scripts/gen_icons.mjs 生成，不要手工编辑；改图标请改脚本里的 GROUPS 再重跑：',
    ' *     node scripts/gen_icons.mjs <remixicon 包目录>',
    ' *',
    ' * 来源：Remix Icon v4.9.1（https://remixicon.com），Copyright (c) 2017–2026 Remix Design。',
    ' * 许可：Remix Icon License v1.0（2026 年 1 月版，全文见同目录 NOTICE.txt）。',
    ' *',
    ' * 该许可允许在 Web 应用/UI 中作功能与装饰元素使用，并允许修改颜色与尺寸（第 2.1、2.2 条）；',
    ' * 但第 3.3 条**禁止**将图标用作 logo / 商标 / 品牌标识，第 4.1 条要求品牌图标仅用于指代该品牌。',
    ' * 因此本文件中的图形只用于界面控件与状态提示，项目品牌标识与 favicon 保持原创。',
    ' *',
    ' * 每个条目都是从 icons/<分类>/<名称>.svg 中抽出的单条 <path d="...">（viewBox 24×24），',
    ' * 由 components/Icon.vue 以 <svg viewBox="0 0 24 24" fill="currentColor"><path :d/></svg> 渲染。',
    ' */',
    '',
    'export const ICON_PATHS = {',
  ]
  for (const [title, names] of GROUPS) {
    lines.push(`  // ---------- ${title} ----------`)
    for (const name of names) {
      lines.push(`  '${name}':`)
      lines.push(`    '${paths[name]}',`)
    }
  }
  lines.push('}')
  lines.push('')
  lines.push('/** 全部可用图标名（供调试与自检使用） */')
  lines.push('export const ICON_NAMES = Object.keys(ICON_PATHS)')
  lines.push('')

  writeFileSync(OUT_FILE, lines.join('\n'), 'utf8')
  console.log(`已生成 ${OUT_FILE.slice(ROOT.length + 1)}`)
  console.log(`图标数：${allNames.length}；代码中已引用：${used.size} 个`)
  if (unused.length) console.log(`清单中暂未引用（保留备用）：${unused.join(', ')}`)
}

main()
