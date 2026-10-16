<script setup>
/**
 * components/UploadArtifactsDialog.vue — 「上传爬取数据」弹窗
 *
 * 契约 4.5.1：multipart 字段 `files`（可多个，.csv/.txt/.tsv/.xlsx，单个 ≤20MB、合计 ≤50MB）
 * 与可选 `name`（留空则由后端显示「未自定义数据」）。
 * 本组件只负责收集输入并派发 submit，请求与错误处理交给父组件（useArtifacts.upload）。
 */
import { computed, ref } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  uploading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'submit'])

const fileInput = ref(null)
const files = ref([])
const name = ref('')

const totalSize = computed(() => files.value.reduce((acc, file) => acc + file.size, 0))

function formatSize(bytes) {
  const value = Number(bytes) || 0
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(2)} MB`
}

function onPick(event) {
  files.value = Array.from(event.target.files || [])
}

function removeFile(index) {
  files.value = files.value.filter((_, i) => i !== index)
  if (fileInput.value) fileInput.value.value = ''
}

function submit() {
  if (!files.value.length || props.uploading) return
  emit('submit', { files: files.value, name: name.value })
}
</script>

<template>
  <Teleport to="body">
    <div class="modal" role="dialog" aria-modal="true" aria-label="上传爬取数据" @click.self="emit('close')">
      <div class="modal__panel">
        <header class="modal__head">
          <h2><Icon name="upload-cloud-2-line" :size="17" /> 上传爬取数据</h2>
          <button
            type="button"
            class="icon-btn modal__close"
            title="关闭"
            aria-label="关闭"
            @click="emit('close')"
          >
            <Icon name="close-line" :size="16" />
          </button>
        </header>

        <div class="modal__body">
          <label class="field">
            <span class="field__label">数据文件（可多选）</span>
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".csv,.txt,.tsv,.xlsx"
              :disabled="uploading"
              @change="onPick"
            />
            <span class="modal__hint">支持 .csv / .txt / .tsv / .xlsx；单个 ≤ 20 MB，合计 ≤ 50 MB。</span>
          </label>

          <ul v-if="files.length" class="file-list">
            <li v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-row">
              <Icon name="file-3-line" :size="14" class="file-row__icon" />
              <span class="file-row__name mono">{{ file.name }}</span>
              <span class="file-row__size">{{ formatSize(file.size) }}</span>
              <button
                type="button"
                class="icon-btn icon-btn--danger file-row__remove"
                :disabled="uploading"
                :title="`移除 ${file.name}`"
                :aria-label="`移除 ${file.name}`"
                @click="removeFile(index)"
              >
                <Icon name="close-line" :size="14" />
              </button>
            </li>
          </ul>
          <p v-else class="modal__empty">尚未选择文件。</p>

          <label class="field">
            <span class="field__label">项目名称（可选）</span>
            <input v-model="name" type="text" placeholder="留空则显示「未自定义数据」" :disabled="uploading" />
          </label>

          <p v-if="error" class="hint hint--error">{{ error }}</p>
        </div>

        <footer class="modal__foot">
          <span class="modal__total">
            已选 {{ files.length }} 个文件 · 合计 {{ formatSize(totalSize) }}
          </span>
          <div class="modal__actions">
            <button type="button" class="btn" :disabled="uploading" @click="emit('close')">取消</button>
            <button type="button" class="btn btn--primary" :disabled="!files.length || uploading" @click="submit">
              <Icon name="upload-2-line" :size="14" />
              {{ uploading ? '上传中…' : '上传并处理' }}
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(15, 23, 42, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal__panel {
  width: min(560px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
  border: 1px solid var(--border);
}
.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}
.modal__head h2 {
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.modal__close {
  color: var(--text-muted);
}
.modal__close:hover:not(:disabled) {
  background: var(--negative-soft);
  border-color: #fecaca;
  color: var(--negative);
}
.modal__body {
  padding: 16px 18px;
}
.modal__hint {
  display: block;
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12.5px;
}
.file-list {
  list-style: none;
  margin: 0 0 14px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 180px;
  overflow-y: auto;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-alt);
  font-size: 13px;
}
.file-row__icon {
  color: var(--text-muted);
}
.file-row__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-row__size {
  color: var(--text-muted);
  font-size: 12.5px;
}
.file-row__remove {
  /* 命中区/悬停态来自全局 .icon-btn--danger */
  flex: 0 0 auto;
}
.modal__empty {
  margin: 0 0 14px;
  color: var(--text-muted);
  font-size: 13px;
}
.modal__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
  background: var(--surface-alt);
}
.modal__total {
  color: var(--text-muted);
  font-size: 12.5px;
}
.modal__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
