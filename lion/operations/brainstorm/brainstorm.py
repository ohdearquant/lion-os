import json
from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar

from lion.core.communication.message import Note
from lion.core.forms.form import OperativeForm
from lion.core.session.branch import Branch
from lion.core.session.session import Session
from lion.core.types import ID
from lion.libs.func import alcall
from lion.protocols.operatives.instruct import INSTRUCT_MODEL_FIELD, InstructModel
from lion.protocols.operatives.reason import ReasonModel

from .prompt import PROMPT

T = TypeVar("T")


async def run_instruct(
    ins: InstructModel,
    session: Session,
    branch: Branch,
    auto_run: bool,
    verbose: bool = False,
    **kwargs: Any,
) -> Any:
    """Execute an instruction within a brainstorming session.

    Args:
        ins: The instruction model to run.
        session: The current session.
        branch: The branch to operate on.
        auto_run: Whether to automatically run nested instructions.
        verbose: Whether to enable verbose output.
        **kwargs: Additional keyword arguments.

    Returns:
        The result of the instruction execution.
    """
    if verbose:
        guidance_preview = (
            ins.guidance[:100] + "..." if len(ins.guidance) > 100 else ins.guidance
        )
        print(f"Running instruction: {guidance_preview}")

    async def run(ins_):
        b_ = session.split(branch)
        return await run_instruct(ins_, session, b_, False, **kwargs)

    config = {**ins.model_dump(), **kwargs}
    res = await branch.operate(**config)
    branch.msgs.logger.dump()
    instructs = []

    if hasattr(res, "instruct_models"):
        instructs = res.instruct_models

    if auto_run is True and instructs:
        ress = await alcall(instructs, run)
        response_ = []
        for res in ress:
            if isinstance(res, list):
                response_.extend(res)
            else:
                response_.append(res)
        response_.insert(0, res)
        return response_

    return res


async def brainstorm(
    instruct: InstructModel | dict[str, Any],
    num_instruct: int = 3,
    session: Session | None = None,
    branch: Branch | ID.Ref | None = None,
    auto_run: bool = True,
    branch_kwargs: dict[str, Any] | None = None,
    return_session: bool = False,
    verbose: bool = False,
    **kwargs: Any,
) -> Any:
    """Perform a brainstorming session.

    Args:
        instruct: Instruction model or dictionary.
        num_instruct: Number of instructions to generate.
        session: Existing session or None to create a new one.
        branch: Existing branch or reference.
        auto_run: If True, automatically run generated instructions.
        branch_kwargs: Additional arguments for branch creation.
        return_session: If True, return the session with results.
        verbose: Whether to enable verbose output.
        **kwargs: Additional keyword arguments.

    Returns:
        The results of the brainstorming session, optionally with the session.
    """
    if verbose:
        print(f"Starting brainstorming with {num_instruct} instructions.")

    field_models: list = kwargs.get("field_models", [])
    if INSTRUCT_MODEL_FIELD not in field_models:
        field_models.append(INSTRUCT_MODEL_FIELD)

    kwargs["field_models"] = field_models

    if session is not None:
        if branch is not None:
            branch: Branch = session.branches[branch]
        else:
            branch = session.new_branch(**(branch_kwargs or {}))
    else:
        session = Session()
        if isinstance(branch, Branch):
            session.branches.include(branch)
            session.default_branch = branch
        if branch is None:
            branch = session.new_branch(**(branch_kwargs or {}))

    if isinstance(instruct, InstructModel):
        instruct = instruct.clean_dump()
    if not isinstance(instruct, dict):
        raise ValueError(
            "instruct needs to be an InstructModel obj or a dictionary of valid parameters"
        )

    guidance = instruct.get("guidance", "")
    instruct["guidance"] = f"\n{PROMPT.format(num_instruct=num_instruct)}" + guidance

    res1 = await branch.operate(**instruct, **kwargs)
    if verbose:
        print("Initial brainstorming complete.")

    instructs = None

    async def run(ins_):
        b_ = session.split(branch)
        return await run_instruct(
            ins_, session, b_, auto_run, verbose=verbose, **kwargs
        )

    if not auto_run:
        return res1

    async with session.branches:
        if hasattr(res1, "instruct_models"):
            instructs: list[InstructModel] = res1.instruct_models
            ress = await alcall(instructs, run)
            response_ = []

            for res in ress:
                if isinstance(res, list):
                    response_.extend(res)
                else:
                    response_.append(res)

            response_.insert(0, res1)
            if return_session:
                return response_, session
            return response_

    if return_session:
        return res1, session

    return res1


class BrainstormForm(OperativeForm, Generic[T]):
    """Enhanced form for brainstorming operations with improved filtering and persistence."""

    operation_type = "brainstorm"
    context: Note
    confidence_score: float
    reasoning: ReasonModel

    def __init__(
        self,
        context: Note,
        num_ideas: int,
        idea_filters: list[str] | None = None,
        guidance: str | None = None,
        min_confidence_score: float = 0.5,
        max_similar_ideas: int = 2,
    ):
        """Initialize the brainstorm form with enhanced parameters.

        Args:
            context: The context for brainstorming
            num_ideas: Number of ideas to generate
            idea_filters: List of filter criteria
            guidance: Optional guidance for idea generation
            min_confidence_score: Minimum confidence score for ideas
            max_similar_ideas: Maximum number of similar ideas allowed
        """
        super().__init__()
        self.context = context
        self.num_ideas = num_ideas
        self.idea_filters = idea_filters or []
        self.guidance = guidance
        self.result: list[InstructModel] | None = None
        self.min_confidence_score = min_confidence_score
        self.max_similar_ideas = max_similar_ideas
        self.metrics: dict[str, Any] = {}

    async def execute(self) -> list[InstructModel]:
        """Execute the brainstorming process with enhanced error handling and metrics.

        Returns:
            List[InstructModel]: Filtered and validated ideas

        Raises:
            ValueError: If the context or parameters are invalid
            RuntimeError: If the brainstorming process fails
        """
        try:
            # Start metrics tracking
            start_time = datetime.now()

            # Validate inputs
            if not self.context or not isinstance(self.context, Note):
                raise ValueError("Invalid context provided")

            instruct = InstructModel(
                instruction=self.guidance
                or "Please brainstorm ideas based on the context",
                context=self.context,
            )

            # Generate ideas
            ideas = await brainstorm(
                instruct=instruct, num_instruct=self.num_ideas, auto_run=False
            )

            # Apply filters and validate
            filtered_ideas = self.apply_filters(ideas)
            validated_ideas = self.validate_ideas(filtered_ideas)

            # Update metrics
            self.metrics.update(
                {
                    "execution_time": (datetime.now() - start_time).total_seconds(),
                    "initial_ideas": len(ideas) if isinstance(ideas, list) else 1,
                    "filtered_ideas": len(filtered_ideas),
                    "final_ideas": len(validated_ideas),
                    "timestamp": datetime.now().isoformat(),
                }
            )

            self.result = validated_ideas
            return self.result

        except Exception as e:
            self.metrics["error"] = str(e)
            raise RuntimeError(f"Brainstorming process failed: {str(e)}")

    def apply_filters(self, ideas: Any) -> list[InstructModel]:
        """Apply enhanced filtering to ideas with multiple criteria.

        Args:
            ideas: Raw ideas to filter

        Returns:
            List[InstructModel]: Filtered ideas meeting all criteria
        """
        if not isinstance(ideas, (list, InstructModel)):
            return []

        ideas_list = [ideas] if isinstance(ideas, InstructModel) else ideas
        filtered_ideas = []

        for idea in ideas_list:
            if self._passes_filters(idea):
                filtered_ideas.append(idea)

        return filtered_ideas

    def validate_ideas(self, ideas: list[InstructModel]) -> list[InstructModel]:
        """Validate ideas against quality criteria.

        Args:
            ideas: List of ideas to validate

        Returns:
            List[InstructModel]: Validated ideas meeting quality criteria
        """
        validated_ideas = []
        similarity_groups = {}

        for idea in ideas:
            # Check confidence score
            if (
                hasattr(idea, "confidence_score")
                and idea.confidence_score >= self.min_confidence_score
            ):
                # Group similar ideas
                key = self._get_similarity_key(idea)
                if key not in similarity_groups:
                    similarity_groups[key] = []

                if len(similarity_groups[key]) < self.max_similar_ideas:
                    similarity_groups[key].append(idea)
                    validated_ideas.append(idea)

        return validated_ideas

    def _passes_filters(self, idea: InstructModel) -> bool:
        """Check if an idea passes all defined filters.

        Args:
            idea: Idea to check

        Returns:
            bool: True if idea passes all filters
        """
        for filter_criterion in self.idea_filters:
            if not self._apply_filter(idea, filter_criterion):
                return False
        return True

    def _apply_filter(self, idea: InstructModel, filter_criterion: str) -> bool:
        """Apply a specific filter criterion to an idea.

        Args:
            idea: Idea to filter
            filter_criterion: Filter criterion to apply

        Returns:
            bool: True if idea passes the filter
        """
        # Implement specific filter logic based on criterion
        if filter_criterion == "non_empty":
            return bool(getattr(idea, "instruction", "").strip())
        elif filter_criterion == "has_context":
            return hasattr(idea, "context") and bool(idea.context)
        # Add more filter criteria as needed
        return True

    def _get_similarity_key(self, idea: InstructModel) -> str:
        """Generate a key for grouping similar ideas.

        Args:
            idea: Idea to generate key for

        Returns:
            str: Similarity key
        """
        # Implement similarity detection logic
        # This is a simple implementation that could be enhanced
        return getattr(idea, "instruction", "")[:50].lower()

    def save_session(self, filepath: str) -> None:
        """Save the brainstorming session results and metrics.

        Args:
            filepath: Path to save the session data
        """
        session_data = {
            "metrics": self.metrics,
            "results": [idea.model_dump() for idea in (self.result or [])],
            "filters": self.idea_filters,
            "parameters": {
                "num_ideas": self.num_ideas,
                "min_confidence_score": self.min_confidence_score,
                "max_similar_ideas": self.max_similar_ideas,
            },
        }

        with open(filepath, "w") as f:
            json.dump(session_data, f, indent=2)

    def load_session(self, filepath: str) -> None:
        """Load a previously saved brainstorming session.

        Args:
            filepath: Path to load the session data from
        """
        with open(filepath) as f:
            session_data = json.load(f)

        self.metrics = session_data.get("metrics", {})
        self.idea_filters = session_data.get("filters", [])
        params = session_data.get("parameters", {})
        self.num_ideas = params.get("num_ideas", self.num_ideas)
        self.min_confidence_score = params.get(
            "min_confidence_score", self.min_confidence_score
        )
        self.max_similar_ideas = params.get("max_similar_ideas", self.max_similar_ideas)
