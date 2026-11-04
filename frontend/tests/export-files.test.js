/**
 * frontend/tests/export-files.test.js
 *
 * 回归测试：导出 / Python 词云图 URL 构造与词云版本号（纯函数，零新依赖）。
 * 契约见 docs/superpowers/specs/2026-11-04-export-and-wordcloud-design.md 第 3、4 节：
 *   GET /api/artifacts/{id}/download?stage=initial|clean|excluded
 *   GET /api/videos/{bvid}/download?stage=crawl|clean
 *   GET /api/artifacts/{id}/wordcloud-image?tone=&v=&download=
 *   GET /api/videos/{bvid}/wordcloud-image?tone=&v=&download=
 *
 * 运行：cd frontend && npm test（Node 内置 node:test）
 */
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

test('artifactDownloadUrl 默认导出清洗结果', () => {
  assert.equal(artifactDownloadUrl('p_1'), '/api/artifacts/p_1/download?stage=clean')
})

test('videoDownloadUrl 默认 crawl', () => {
  assert.equal(videoDownloadUrl('BV1xx411c7mD'), '/api/videos/BV1xx411c7mD/download?stage=crawl')
  assert.equal(videoDownloadUrl('BV1xx411c7mD', 'clean'), '/api/videos/BV1xx411c7mD/download?stage=clean')
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

test('wordcloudImageUrl 数据项目走 artifacts 路径，且默认 tone=positive', () => {
  const url = wordcloudImageUrl({ kind: 'artifact', id: 'p_1' })
  assert.match(url, /^\/api\/artifacts\/p_1\/wordcloud-image\?tone=positive$/)
  assert.ok(!url.includes('download='), '非下载场景不应带 download 参数')
})

test('keywordsRevision 随词内容变化且确定', () => {
  const words = [{ word: '清晰', count: 10 }, { word: '实用', count: 4 }]
  assert.equal(keywordsRevision(words), keywordsRevision(words))
  assert.notEqual(keywordsRevision(words), keywordsRevision([{ word: '清晰', count: 9 }]))
})

test('keywordsRevision 容忍空值与非法条目', () => {
  assert.equal(keywordsRevision(undefined), '0-0-0')
  assert.equal(keywordsRevision([]), '0-0-0')
  assert.equal(keywordsRevision([null, { count: '2' }, { word: '好', count: 'x' }]), '3-2-1')
})
