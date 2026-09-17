""" 
end call tool for the conversation agent , 
Ends the call based on the appropriate context of the call 
""" 

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import EndWorkerFrame


def create_end_call_tool(call_session=None):
    """Build the end-call tool bound to this call's session (for end-reason tracking)."""

    @tool_options(cancel_on_interruption=True)
    async def dynamic_end_call(params : FunctionCallParams):
        """Disconnects the current phone call.

        Call this tool ONLY on the final farewell turn when the conversation is completely finished:
        1. Standard close: meeting is confirmed + you asked "Is there anything else?" + user answered no / goodbye. (Do NOT call when asking "Anything else?", only on the NEXT turn after they answer!).
        2. Wrong number: user says it's a wrong number or they're not the person.
        3. Confirmed disinterest: user clearly declined after pitch and confirmed no interest.
        4. User is in a meeting / call back later: user said they can't talk, you acknowledged and said farewell.
        5. 'Send me an email' follow-up: info requested, farewell spoken.
        6. Hostile / asked to be removed from list: you acknowledged and said goodbye.

        ABSOLUTELY FORBIDDEN — Do NOT call this tool for:
        - While negotiating, discussing, or confirming the meeting day or time!
        - If your turn asks ANY question (e.g. asking "is there anything else before we wrap up?", asking for day/time, asking for clarification). Calling end_call while asking a question hangs up on the user mid-sentence!
        - Mid-conversation responses where the user is still engaged.

        Speak your farewell message FIRST, then call this tool in the same response turn.
        """
        if call_session is not None:
            call_session.end_reason = "user_ended"

        await params.result_callback({
            "success" : True
        })

        await params.llm.push_frame(EndWorkerFrame())

    return dynamic_end_call



