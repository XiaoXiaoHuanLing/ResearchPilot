"""RAG 评估模块主入口。

内部实现已经压平成同级文件：
- `dataset_builder.py`
- `metrics.py`
- `reporting.py`
- `rag_eval.py`
"""

from eval.dataset_builder import (
    EvalSample,
    build_formal_dataset,
    load_nodes_from_docstore,
    normalize_dataset,
    normalize_sample,
    save_dataset,
    validate_dataset,
    validate_samples,
    _generate_samples_for_group,
)
from eval.metrics import (
    compute_answer_fact_coverage,
    evaluate_retrieval_sample,
    run_answer_evaluation,
    run_e2e_evaluation,
    run_retrieval_comparison,
)
from eval.reporting import (
    build_analysis_notes,
    build_summary_markdown,
    generate_report_files,
)

__all__ = [
    "EvalSample",
    "build_formal_dataset",
    "load_nodes_from_docstore",
    "normalize_dataset",
    "normalize_sample",
    "save_dataset",
    "validate_dataset",
    "validate_samples",
    "_generate_samples_for_group",
    "compute_answer_fact_coverage",
    "evaluate_retrieval_sample",
    "run_answer_evaluation",
    "run_e2e_evaluation",
    "run_retrieval_comparison",
    "build_analysis_notes",
    "build_summary_markdown",
    "generate_report_files",
]
