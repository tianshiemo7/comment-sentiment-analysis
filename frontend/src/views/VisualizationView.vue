<script setup>
/**
 * 可视化页：骨架阶段仅做四个占位卡片，图表由 Python 端
 * src/visualization/ 生成到 outputs/figures/。
 * 锁定的接口契约中【没有】图表接口，因此本页不做任何网络请求，
 * 只声明卡片与对应产物文件路径。
 */
import Icon from '../components/Icon.vue'

const figures = [
  {
    key: 'curves',
    title: '损失 / 准确率曲线',
    icon: 'line-chart-line',
    file: 'outputs/figures/loss_curve.png、accuracy_curve.png',
    desc: '训练集与验证集的 loss、accuracy 随 epoch 变化曲线，用于观察收敛与过拟合。',
    source: 'src/visualization/（训练曲线）',
  },
  {
    key: 'confusion',
    title: '混淆矩阵热力图',
    icon: 'table-line',
    file: 'outputs/figures/confusion_matrix_*.png',
    desc: '正负样本的预测分布热力图（每个模型一份，如 confusion_matrix_cnn.png、confusion_matrix_bilstm_attention.png）。',
    source: 'src/evaluation/ + src/visualization/',
  },
  {
    key: 'wordcloud',
    title: '正负评论词云',
    icon: 'cloud-line',
    file: 'outputs/figures/wordcloud_positive.png、wordcloud_negative.png',
    desc: '正面与负面评论的高频词分布，用于结果解释与文本分析。',
    source: 'src/visualization/（词云）',
  },
  {
    key: 'attention',
    title: 'Attention 权重可视化',
    icon: 'bar-chart-horizontal-line',
    file: 'outputs/figures/attention_heatmap.png',
    desc: 'BiLSTM-Attention 模型对输入词序列的注意力权重（可选加分项）。',
    source: 'src/models/ + src/visualization/',
  },
]
</script>

<template>
  <section class="stack">
    <div class="page-head">
      <h1>可视化</h1>
      <p>
        对应课程要求的四项结果可视化。图表由 Python 端 <code>src/visualization/</code>
        生成到 <code>outputs/figures/</code>，前端当前仅提供占位卡片与文件对应关系。
      </p>
    </div>

    <div class="hint hint--warn">
      <strong>待接入后端</strong>：锁定的接口契约中没有图表接口，本页不发起请求。
      后端产出 PNG 后，可考虑两种接入方式（<strong>需 Lead 决策</strong>）：
      ① FastAPI 静态挂载 <code>outputs/figures/</code>，前端 <code>&lt;img&gt;</code> 直接引用；
      ② 新增只读接口返回图表清单。图表库（ECharts）按课程安排后续自行接入。
    </div>

    <div class="grid-4">
      <div v-for="item in figures" :key="item.key" class="card">
        <div class="card__head">
          <h3><Icon :name="item.icon" :size="16" /> {{ item.title }}</h3>
        </div>
        <div class="figure-placeholder">
          <Icon name="image-line" :size="26" />
          <span>图表待生成</span>
          <span class="figure-placeholder__file">{{ item.file }}</span>
        </div>
        <p class="card__hint" style="margin: 12px 0 0">{{ item.desc }}</p>
        <div class="card__foot">
          生成位置：<span class="mono">{{ item.source }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="file-list-3-line" :size="16" /> 产物文件对照表</h2>
        <span class="card__hint">命名约定见 outputs/README.md</span>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 200px">图表面板</th>
              <th>产物文件（仓库相对路径）</th>
              <th style="width: 260px">生成位置</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in figures" :key="item.key">
              <td>{{ item.title }}</td>
              <td class="mono">{{ item.file }}</td>
              <td class="mono">{{ item.source }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="card__foot">
        训练日志见 <code>outputs/logs/</code>，数据统计见 <code>outputs/stats/dataset_stats.json</code>。
      </div>
    </div>
  </section>
</template>
