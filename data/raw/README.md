# 原始评论数据登记

文件：BV1UB4y1W7Xu_comments.csv。

本文件由用户提供，并从用户指定的本地文件复制到本目录；本步骤没有重新采集，也没有修改原始CSV。
SHA256：075d7a9c69e533a44e4294c66dbc78727242ca357974850f67d1acc9e6593697。

共154条评论，视频编号为BV1UB4y1W7Xu。其中level=1为99条，level=2为55条，无情感标签。
采集时间与评论时间分别保存在collected_at_utc、ctime_utc；点赞与回复关系等原始字段同时保留。

处理命令：

```bash
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean
```

首次处理保留149条，排除5条完全重复的清洗文本；被排除记录在comments_excluded.csv中保留，原始CSV仍完整保存154条。
清洗后保留97条一级评论和52条二级回复；默认保留回复层级，未把无标签评论当作监督训练集。

下游读取data/processed/comments_cleaned.csv，使用已训练模型的预处理器处理text_raw再进行情感预测。
该CSV及处理产物被.gitignore忽略，不随源码自动提交。
