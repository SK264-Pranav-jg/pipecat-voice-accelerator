""" 
end call tool for the conversation agent , 
Ends the call based on the appropriate context of the call 
""" 

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import EndWorkerFrame
from pipecat.processors.frame_processor import FrameDirection


def create_end_call_tool(call_session=None):
    """Build the end-call tool bound to this call's session (for end-reason tracking)."""

    @tool_options(cancel_on_interruption=True)
    async def dynamic_end_call(params : FunctionCallParams):
        """Disconnects the current phone call.

        Call this tool ONLY on the final farewell turn when the conversation is completely finished:
        Call this tool ONLY once during a conversation 
        Always ask if the user would like to end the conversation before calling this tool 
        DO not continue the conversation after this tool has been called 
        

        Most importantly 
        Speak your farewell message FIRST, then call this tool in the same response turn.
        """
        if call_session is not None:
            call_session.end_reason = "user_ended"

        await params.result_callback({
            "success" : True
        })

        await params.llm.push_frame(EndWorkerFrame())

    return dynamic_end_call



