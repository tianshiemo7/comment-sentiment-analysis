/**
 * frontend/tests/prob-bars.test.js
 *
 * 回归测试：单条预测页的置信度条必须真的可见。
 *
 * 背景 bug：.prob-row__fill 在模板里是 <span>，而 main.css 只给了 height/background，
 * 没有 display。内联元素上的 width: 3.17% 不生效，Playwright 实测
 * getBoundingClientRect().width === 0，页面只剩灰色轨道。
 * 这里直接锁住 main.css 的关键声明，防止「删掉 display: block」式回退。
 *
 * 运行：node --test tests/prob-bars.test.js（零新依赖，只用 Node 内置 node:test）
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const cssPath = new URL('../src/assets/main.css', import.meta.url)
const cssText = readFileSync(cssPath, 'utf8')

/**
 * 取出某个「单类选择器」规则的声明块。
 * 用 (?:^|[}\s,*]) + 尾部 \s*\{ 约束，确保 `.prob-row` 不会误匹配 `.prob-row__bar`，
 * `.prob-row__fill` 也不会误匹配 `.prob-row__fill--negative`。
 * @param {string} css
 * @param {string} className 不带前导点的类名
 * @returns {string|null}
 */
function extractRule(css, className) {
  const escaped = className.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(?:^|[}\\s,*])\\.${escaped}\\s*\\{([^}]*)\\}`, 'm')
  const match = css.match(re)
  // 注释里出现的 "display:inline" 等字样不能算作声明
  return match ? match[1].replace(/\/\*[\s\S]*?\*\//g, '') : null
}

/** 声明块里是否包含 `property: value`（容忍空白/大小写/结尾分号）。 */
function hasDeclaration(body, property, value) {
  const re = new RegExp(`(?:^|;|\\{)\\s*${property}\\s*:\\s*${value}\\s*(?:;|$)`, 'mi')
  return re.test(body)
}

test('.prob-row__fill 显式 display: block（<span> 内联时宽度百分比会失效）', () => {
  const body = extractRule(cssText, 'prob-row__fill')
  assert.ok(body, 'main.css 中必须存在 .prob-row__fill 规则')
  assert.ok(
    hasDeclaration(body, 'display', 'block'),
    '.prob-row__fill 必须声明 display: block，否则 span 上的 width 百分比不生效',
  )
  assert.ok(!/display\s*:\s*inline/i.test(body), '.prob-row__fill 不能是 display: inline')
})

test('.prob-row__fill 撑满轨道高度（height: 100%）', () => {
  const body = extractRule(cssText, 'prob-row__fill')
  assert.ok(body, 'main.css 中必须存在 .prob-row__fill 规则')
  assert.ok(
    hasDeclaration(body, 'height', '100%'),
    '.prob-row__fill 必须 height: 100%，否则轨道内没有可见高度',
  )
})

test('.prob-row 使用 grid 布局，置信度条占据 1fr 列', () => {
  const body = extractRule(cssText, 'prob-row')
  assert.ok(body, 'main.css 中必须存在 .prob-row 规则')
  assert.ok(
    hasDeclaration(body, 'display', 'grid'),
    '.prob-row 必须 display: grid（名称 / 条 / 数值三列对齐）',
  )
  assert.match(body, /grid-template-columns\s*:[^;]*1fr/i, '.prob-row 的轨道列必须是 1fr')
})

test('.prob-row__bar 有确定高度并 overflow: hidden（填充被裁剪成圆角轨道）', () => {
  const body = extractRule(cssText, 'prob-row__bar')
  assert.ok(body, 'main.css 中必须存在 .prob-row__bar 规则')
  assert.match(body, /(?:^|;|\{)\s*height\s*:\s*[^;]+/i, '.prob-row__bar 必须有确定高度')
  assert.ok(
    hasDeclaration(body, 'overflow', 'hidden'),
    '.prob-row__bar 必须 overflow: hidden，避免窄条填充溢出圆角',
  )
})
