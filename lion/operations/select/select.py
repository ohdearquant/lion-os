from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from lion import Branch
from lion.protocols.operatives.instruct import InstructModel

from .prompt import PROMPT
from .utils import parse_selection, parse_to_representation


class SelectionModel(BaseModel):
    """Model representing the selection output."""

    selected: list[Any] = Field(default_factory=list)


class SelectOperation:
    """Operation for making selections from a set of choices."""

    def __init__(self):
        self.prompt = PROMPT

    async def __call__(
        self,
        instruct: InstructModel | dict[str, Any],
        choices: list[str] | type[Enum] | dict[str, Any],
        max_num_selections: int = 1,
        branch: Branch | None = None,
        branch_kwargs: dict[str, Any] | None = None,
        return_branch: bool = False,
        verbose: bool = False,
        **kwargs: Any,
    ) -> SelectionModel | tuple[SelectionModel, Branch]:
        """Perform a selection operation from given choices.

        Args:
            instruct: Instruction model or dictionary.
            choices: Options to select from.
            max_num_selections: Maximum selections allowed.
            branch: Existing branch or None to create a new one.
            branch_kwargs: Additional arguments for branch creation.
            return_branch: If True, return the branch with the selection.
            verbose: Whether to enable verbose output.
            **kwargs: Additional keyword arguments.

        Returns:
            A SelectionModel instance, optionally with the branch.
        """
        if verbose:
            print(f"Starting selection with up to {max_num_selections} choices.")

        # Initialize branch
        branch = branch or Branch(**(branch_kwargs or {}))

        # Parse choices and prepare prompt
        selections, contents = parse_to_representation(choices)
        prompt = self.prompt.format(
            max_num_selections=max_num_selections, choices=selections
        )

        # Prepare instruction
        if isinstance(instruct, InstructModel):
            instruct = instruct.clean_dump()
        instruct = instruct or {}

        # Add prompt to instruction
        if instruct.get("instruction", None) is not None:
            instruct["instruction"] = f"{instruct['instruction']}\n\n{prompt}\n\n"
        else:
            instruct["instruction"] = prompt

        # Prepare context with choices
        context = instruct.get("context", None) or []
        context = [context] if not isinstance(context, list) else context
        context.extend([{k: v} for k, v in zip(selections, contents)])
        instruct["context"] = context

        # Execute selection operation
        response_model: SelectionModel = await branch.operate(
            operative_model=SelectionModel,
            **kwargs,
            **instruct,
        )
        if verbose:
            print(f"Received selection: {response_model.selected}")

        # Process selections
        selected = response_model
        if isinstance(response_model, BaseModel) and hasattr(
            response_model, "selected"
        ):
            selected = response_model.selected
        selected = [selected] if not isinstance(selected, list) else selected

        # Parse and correct selections
        corrected_selections = [parse_selection(i, choices) for i in selected]

        # Update response model with corrected selections
        if isinstance(response_model, BaseModel):
            response_model.selected = corrected_selections
        elif isinstance(response_model, dict):
            response_model["selected"] = corrected_selections

        if return_branch:
            return response_model, branch
        return response_model


# Create a singleton instance for the default select operation
select = SelectOperation()
