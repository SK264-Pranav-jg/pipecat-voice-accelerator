""" 
end call tool for the conversation agent , 
Ends the call based on the appropriate context of the call 
""" 

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import EndWorkerFrame , FunctionCallResultProperties 
from pipecat.processors.frame_processor import FrameDirection 

import logging

logger = logging.getLogger(__name__)

def create_end_call_tool(call_session=None):
    """Build the end-call tool bound to this call's session (for end-reason tracking)."""

    @tool_options(cancel_on_interruption=False)
    async def dynamic_end_call(params : FunctionCallParams):
        """
        End the current phone call.

        Use only when the conversation should stop and no further interaction with the
        caller is required.

        WHEN TO USE:
        - The caller explicitly asks to end the call.
        - The caller says goodbye or clearly indicates that they are finished.
        - The agent has completed the requested task and the caller has no
        remaining questions.
        - The conversation has reached its intended conclusion.
        - A workflow or business rule requires the call to terminate.

        WHEN NOT TO USE:
        - Do not end the call just because one question has been answered.
        - Do not end the call if the caller appears to have another question.
        - Do not end the call because the agent is waiting for a response.
        - Do not end the call simply because the conversation is taking longer
        than expected.
        - Do not call this tool multiple times in the same turn or across turns 

        CALL CLOSING:
        Speak your one brief farewell (e.g. "Thank you for your time. Have a great day.")
        AND invoke this tool in the SAME turn/response. Do not wait for the tool result
        before speaking — say the farewell and call the tool together.

        Call this tool at most once per call. Never call it again after it has
        already been invoked, and never speak a second farewell.

        Args:
            reason: Briefly describe why the call is being ended. This is
                used for logging, monitoring, and post-call analysis.
                Examples:
                - "Caller requested to end the call"
                - "Caller said goodbye"
                - "Task completed"
                - "Conversation completed"
                - "Caller stopped responding"

        Returns:
            A confirmation that the call termination request was accepted
            and the call is being ended.
        """
        logger.info(f"Calling end call tool with call session {call_session}")
        if call_session is not None:
            call_session.end_reason = "user_ended"

        # run_llm=False — without it, the function-call runner's default
        # behavior re-invokes the LLM right after this result, which (primed by
        # the farewell instructions already in context) speaks a second,
        # redundant goodbye before EndWorkerFrame actually tears the pipeline down.
        await params.result_callback(
            {"success": True},
            properties=FunctionCallResultProperties(run_llm=False),
        )

        await params.llm.push_frame(EndWorkerFrame())

    return dynamic_end_call



