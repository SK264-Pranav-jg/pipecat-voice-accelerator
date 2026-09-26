""" 
agent tool to get the current date and time 

Provides the LLM agent with real-time awareness of current date, day of week,
and timestamps for scheduling callbacks, meetings, and temporal reasoning.

""" 
import zoneinfo 
import json 
from datetime import datetime  
from pipecat.services.llm_service import FunctionCallParams 

async def get_current_datetime(params: FunctionCallParams, timezone_str: str = "Asia/Kolkata"):
    """Retrieves the current date, time, day of the week, and timezone.

    Always use this tool whenever temporal context is required, such as:
    - Answering questions about today's date, current time, or current day of the week.
    - Resolving relative time expressions (e.g., "today", "tomorrow", "yesterday",
      "this afternoon", "next Monday").
    - Checking business hours, scheduling callbacks, booking meetings, or discussing deadlines.

    Do not guess or assume the current date or time from training data; always check
    with this tool first for accurate temporal reasoning.

    Args:
        timezone_str: Standard IANA timezone string (e.g., "Asia/Kolkata", "America/New_York",
                      "UTC", "Europe/London"). Defaults to "Asia/Kolkata".

    Returns:
        JSON object containing ISO datetime, formatted date, 12-hour time with AM/PM,
        day of the week, timezone, and timezone abbreviation.
    """ 

    try : 
        tz = zoneinfo.ZoneInfo(timezone_str)
        now = datetime.now(tz) 

        result = {
            "datetime": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%I:%M:%S %p"),  # 12-hour format with AM/PM
            "day_of_week": now.strftime("%A"),
            "timezone": timezone_str,
            "tz_abbreviation": now.strftime("%Z")
        }

        temporal_data = json.dumps(result,indent=2) 
        await params.result_callback(temporal_data) 
    
    except zoneinfo.ZoneInfoNotFoundError:
        await params.result_callback(json.dumps({
            "error" : f"Invalid timezone {timezone_str}"
        }))
    except Exception as e :
        await params.result_callback(json.dumps({
            "error" : f"Unable to get current date and time : {str(e)}"
        }))