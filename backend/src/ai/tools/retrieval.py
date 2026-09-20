""" rag tool for the bot to fetch relevant questions for the queries of the user
for now , have used pgvector , can be changed later on to aws kb or db of choice as per requirement """ 
from pipecat.adapters.schemas.direct_function import tool_options 
from pipecat.services.llm_service import FunctionCallParams 

@tool_options(cancel_on_interruption=True) 
async def query_knowledge_base(params : FunctionCallParams ): 
    """ Answer to specific questions asked by the user by fetching relevant """     
    