"""`python -m app.cli evaluate` and `evaluate-report` (UC-10, TC-10)."""

from datetime import UTC, datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings
from app.db.session import create_engine, create_session_factory
from app.evaluation.adjudication import Adjudication, apply_adjudications, load_adjudications
from app.evaluation.agentiq import AgentIQSystem
from app.evaluation.environment import EvaluatedSystem, Shared
from app.evaluation.human import HumanSummary, load_human_results, summarize_human
from app.evaluation.ivr import IvrSystem
from app.evaluation.report import render_markdown, summary_csv
from app.evaluation.results import EvaluationRun, SystemInfo
from app.evaluation.runner import RunOptions, load_run, run_evaluation, save_run
from app.evaluation.single_agent import SingleAgentSystem
from app.knowledge.embeddings import create_embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.store import ChromaVectorStore
from app.providers.llm import LLMClient, create_llm
from app.providers.sentiment import SentimentClassifier, create_sentiment_classifier

EVAL_DIR = PROJECT_ROOT / "eval"
SYSTEMS = ("agentiq", "single_agent", "ivr")


def build_systems(
    names: list[str], llm: LLMClient | None, classifier: SentimentClassifier
) -> tuple[list[EvaluatedSystem], list[SystemInfo]]:
    systems: list[EvaluatedSystem] = []
    skipped: list[SystemInfo] = []
    for name in names:
        if name == "agentiq":
            systems.append(AgentIQSystem(llm, classifier))
        elif name == "single_agent":
            if llm is None:
                skipped.append(
                    SystemInfo(
                        name=name,
                        label=SingleAgentSystem.label,
                        skipped="it needs a language model: set LLM_API_KEY",
                    )
                )
            else:
                systems.append(SingleAgentSystem(llm))
        elif name == "ivr":
            systems.append(IvrSystem())
        else:
            raise ValueError(f"unknown system {name!r}; choose from {', '.join(SYSTEMS)}")
    return systems, skipped


def default_output() -> Path:
    return EVAL_DIR / "reports" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def write_outputs(
    run: EvaluationRun,
    directory: Path,
    adjudications: list[Adjudication] | None = None,
    human: HumanSummary | None = None,
) -> Path:
    """results.json keeps the automatic judgments; the report and CSV include overrides."""
    save_run(run, directory)
    judged = run.model_copy(deep=True)
    adjudications = adjudications or []
    unmatched = apply_adjudications(judged, adjudications)
    for item in unmatched:
        print(f"Adjudication matched nothing: {item.system} {item.scenario} {item.id}")
    applied = [a for a in adjudications if a not in unmatched]
    (directory / "summary.csv").write_text(summary_csv(judged), encoding="utf-8")
    report = directory / "report.md"
    report.write_text(render_markdown(judged, applied, human) + "\n", encoding="utf-8")
    return report


def _extras(
    adjudications: Path | None, human: Path | None
) -> tuple[list[Adjudication], HumanSummary | None]:
    items = load_adjudications(adjudications) if adjudications else []
    summary = summarize_human(load_human_results(human)) if human else None
    return items, summary


async def evaluate(
    settings: Settings,
    *,
    systems: list[str],
    options: RunOptions,
    output: Path,
    adjudications: Path | None = None,
    human: Path | None = None,
    eval_dir: Path = EVAL_DIR,
) -> Path:
    items, human_summary = _extras(adjudications, human)  # fail early on a bad file
    engine = create_engine(settings)
    embedder = create_embedder(settings)
    llm = create_llm(settings)
    classifier = create_sentiment_classifier(settings)
    shared = Shared(
        settings=settings,
        session_factory=create_session_factory(engine),
        store=ChromaVectorStore(settings.chroma_directory, embedder.name),
        embedder=embedder,
        facts=ProfileFactsIndex(embedder),
    )
    chosen, skipped = build_systems(systems, llm, classifier)
    options.environment = {
        "Language model": settings.llm_model if llm else "none (LLM_API_KEY is not set)",
        "Embeddings": embedder.name,
        "Sentiment classifier": classifier.name,
        "Knowledge threshold": str(settings.knowledge_min_score),
        **options.environment,
    }
    if llm is None:
        print(
            "No LLM_API_KEY: AgentIQ runs without a model and the single-agent baseline is skipped."
        )
    try:
        run = await run_evaluation(
            shared, chosen, eval_dir, options, skipped=skipped, progress=print
        )
    finally:
        if llm is not None:
            await llm.aclose()
        await engine.dispose()
    return write_outputs(run, output, items, human_summary)


def rebuild_report(
    results: Path, output: Path | None, adjudications: Path | None, human: Path | None
) -> Path:
    """Rebuilds report.md and summary.csv from a saved results.json, e.g. after adding an
    adjudication, without running anything again."""
    items, human_summary = _extras(adjudications, human)
    return write_outputs(load_run(results), output or results.parent, items, human_summary)
