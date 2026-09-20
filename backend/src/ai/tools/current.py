""" 
agent tool to get the current date and time 

Provides the LLM agent with real-time awareness of current date, day of week,
and timestamps for scheduling callbacks, meetings, and temporal reasoning.

""" 
import zoneinfo 
import json 
from datetime import datetime  
from pipecat.services.llm_service import FunctionCallParams 

async def get_current_datetime(params : FunctionCallParams , timezone_str : str = "Asia/Kolkata"):  
    """ 
    Tool to get the current date and time 

    Use this tool when proposing or confirming meeting dates and times, or when
    the user asks about dates, days, or schedules.

    Never book a meeting for confirm anything related to temporal information without confirming the current date and time 
    from this tool 

    Args : 
        timezone_str : str = Gets the current timezone of the person the agent is calling 
    
    Returns : 
        A dictionary with the current time and date 
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