"""② 预处理模块：清洗、分词、词表、统计与数据集划分。

模块划分见各文件文档；重量级依赖（jieba / pandas）一律在函数内部导入，
保证无依赖环境也能 `import src.preprocessing`。
"""

from .clean import clean_text, remove_html, remove_special_chars, remove_url
from .dataset import load_records, save_records
from .split import split_dataset
from .stats import dataset_stats
from .tokenize import encode_ids, load_stopwords, tokenize
from .vocab import Vocab, build_vocab

__all__ = [
    "clean_text",
    "remove_html",
    "remove_special_chars",
    "remove_url",
    "tokenize",
    "load_stopwords",
    "encode_ids",
    "Vocab",
    "build_vocab",
    "dataset_stats",
    "split_dataset",
    "load_records",
    "save_records",
]
