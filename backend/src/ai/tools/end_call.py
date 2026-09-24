""" 
end call tool for the conversation agent , 
Ends the call based on the appropriate context of the call 
""" 

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties
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
        - Do not call this tool multiple times in the same turn or across turns.

        CALL CLOSING:
        Do NOT say anything before invoking this tool. Call this tool first.
        After the tool result is returned, say a brief natural closing message such as
        "Thank you for your time. Have a great day." Then stop speaking entirely.
        Do not repeat the closing message.

        Call this tool at most once per farewell. After calling it, do not speak
        again, do not repeat the closing message, and do not call it a second time.

        Args:
            reason: Briefly describe why the call is being ended. This is
                used for logging, monitoring, and post-call analysis.
                Examples:
                - "Caller requested to end the call"
                - "Caller said goodbye"
                - "Task completed"
                - "Conversation completed"
                - "Caller stopped responding"
        """
        logger.info(f"Calling end call tool with call session {call_session}")
        if call_session is not None:
            call_session.end_reason = "user_ended"

    ack_context = LLMContext(messages=[
        {
            "role": "system",
            "content": (
                "Generate a brief, natural, warm farewell message (max 15 words) "
                "to end a phone call. Just the farewell, nothing else."
            )
        },
        {
            "role": "user",
            "content": "The call is ending now. Generate a goodbye message."
        }
    ])
    farewell = await params.llm.run_inference(ack_context)

    if farewell:
        await params.llm.push_frame(TTSSpeakFrame(farewell), FrameDirection.DOWNSTREAM )

    await params.result_callback(
        {"success": True},
        properties=FunctionCallResultProperties(run_llm=False),
    )

    await params.llm.push_frame(EndWorkerFrame())

    return dynamic_end_call



