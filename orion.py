"""import libraries"""
import asyncio
import sys
import logging
import json
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters
from typing import List, Any, Dict, Optional
from contextlib import AsyncExitStack
from langchain_ollama import OllamaLLM


#Configure the logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



class Tool:
    """Represents a tool with its properties and formatting."""

    def __init__(self, name: str, description: str, input_schema: Dict[str, Any]) -> None:
        self.name: str = name
        self.description: str = description
        self.input_schema: Dict[str, Any] = input_schema

    def format_for_llm(self) -> str:
        """Format tool information for LLM.
        
        Returns:
            A formatted string describing the tool.
        """
        args_desc = []
        if 'properties' in self.input_schema:
            for param_name, param_info in self.input_schema['properties'].items():
                arg_desc = f"- {param_name}: {param_info.get('description', 'No description')}"
                if param_name in self.input_schema.get('required', []):
                    arg_desc += " (required)"
                args_desc.append(arg_desc)
        
        return f"""
Tool: {self.name}
Description: {self.description}
Arguments:
{chr(10).join(args_desc)}
"""
    
    def to_ollama_format(self) -> Dict[str, Any]:
        """Convert tool to Ollama function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema
            }
        }

class Orion:
    
    def __init__(self):
        #Initialise session and client objects
        self.session: Optional[ClientSession] = None
        self.stdio = None
        self.write = None
        self.exit_stack = AsyncExitStack()
        self.tools:List[Tool] = []
        self.capabilities = None
        self.llm = None

    async def connect_to_server(self, server_script_path: str):
        """ Connect to an MCP server """

        is_python = server_script_path.endswith('.py')
        if not (is_python):
            raise ValueError("Server script must be a .py file")
        
        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        #initialise session and get capabilities
        init_result = await self.session.initialize()
        self.capabilities = init_result.capabilities

        logger.info("Successfully connected to MCP Server")

        #Initialise the LLM
        self.llm = OllamaLLM(model="llama3.2")

        #List tools 
        await self.list_tools()

    async def list_tools(self) -> List[Tool]:
        """List available tools from the server
        Returns:
        A list of available tools.
            
        Raises:
        RuntimeError: If the server is not initialised
        """
        if not self.session:
            raise RuntimeError(f"Session {self.name} not initialised")

        try:   
            list_tools_result = await self.session.list_tools()
            self.tools = []
        
            #Check if server supports progress tracking
            supports_progress = (
                self.capabilities
                 and hasattr(self.capabilities, 'progress') and
                 self.capabilities.progress
            )

            if supports_progress:
                logger.info(f"Server supports progress progress tracking")

            #extract the tools from the response
            if hasattr(list_tools_result, 'tools'):
                for tool in list_tools_result.tools:
                    tool_obj = Tool(tool.name, tool.description, tool.inputSchema)
                    self.tools.append(tool_obj)
                    logger.info(f"Pulled tool: {tool.name}")
            else:
                logger.warning("tools not found in server repsonse")

            logger.info(f"total tools loaded is {len(self.tools)}")
            return self.tools
        
        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            return []
        
    def _format_tools_for_prompt(self) -> str:
        """Format tools for inclusion in the system prompt."""
        if not self.tools:
            return "No tools available."
        
        tools_text = "Available tools:"
        for tool in self.tools:
            tools_text += tool.format_for_llm()
        
        tools_text += """
To use a tool, respond with a JSON object in this format:
{
    "tool_call": {
        "name": "tool_name",
        "arguments": {
            "param1": "value1",
            "param2": "value2"
        }
    }
}
"""
        return tools_text
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool through the MCP server."""
        if not self.session:
            raise RuntimeError("Session not initialized")
        
        try:
            # Call the tool
            result = await self.session.call_tool(tool_name, arguments)
            
            # Format the result
            if hasattr(result, 'content') and result.content:
                content_parts = []
                for content in result.content:
                    if hasattr(content, 'text'):
                        content_parts.append(content.text)
                    else:
                        content_parts.append(str(content))
                return "\n".join(content_parts)
            else:
                return str(result)
                
        except Exception as e:
            error_msg = f"Error calling tool {tool_name}: {str(e)}"
            logger.error(error_msg)
            return error_msg
    
    def _extract_tool_call(self, response: str) -> Optional[Dict[str, Any]]:
        """Extract tool call from LLM response."""
        try:
            # Check for JSON in the response
            start = response.find('{')
            end = response.rfind('}')
            
            if start != -1 and end != -1:
                json_str = response[start:end + 1]
                parsed = json.loads(json_str)
                
                if 'tool_call' in parsed:
                    return parsed['tool_call']
            
            return None
            
        except json.JSONDecodeError:
            return None
    
    async def process_query(self, query: str) -> str:
        """Process a query with tool calling support."""
        if not self.llm:
            return "LLM not initialized. Connect to server first."
        
        # Create system prompt with tools
        system_prompt = f"""You are an awesome AI assistant with access to tools.

{self._format_tools_for_prompt()}

When you need to use a tool to answer a question, respond with the tool call JSON format shown above.
If you don't need tools, respond normally with a message.
"""
        
        # Create the full prompt
        full_prompt = f"{system_prompt}\n\nUser: {query}\n\nAssistant:"
        
        try:
            # Get response from Ollama
            response = self.llm.invoke(full_prompt)
            
            # Check if response contains a tool call
            tool_call = self._extract_tool_call(response)
            
            if tool_call:
                tool_name = tool_call.get('name')
                arguments = tool_call.get('arguments', {})
                
                logger.info(f"Calling tool: {tool_name} with args: {arguments}")
                
                # Execute the tool
                tool_result = await self.call_tool(tool_name, arguments)
                
                # Get follow-up response from LLM with tool result
                follow_up_prompt = f"""{system_prompt}

User: {query}

Tool call: {tool_name}({json.dumps(arguments)})
Tool result: {tool_result}

Now provide a natural language response to the user based on the tool result:
"""

                return self.llm.invoke(follow_up_prompt)
            else:
                return response

        except Exception as e:
            logger.error(f"Error processing query: {e}")
            return "Sorry, something went wrong."
    


    
async def main():
    if len(sys.argv) < 2:
        logger.info("Usage python orion.py mcp_server.py")
        return
        
    client = Orion()
    await client.connect_to_server("mcp_server.py")

    while True:
        try:
            user_input = input("You: ").strip().lower()
            if user_input in ['quit', 'exit']:
                logging.info("\nExiting...")
                break
            response = await client.process_query(user_input)
            logger.info(f"Orion: {response}")
                
        except (KeyboardInterrupt, EOFError):
            break

if __name__ == "__main__":
    asyncio.run(main())