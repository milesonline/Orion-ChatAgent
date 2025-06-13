"""import libraries"""
import json
import os
import logging
import httpx
import yaml
from typing import List, Any, Dict, Optional, Union
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool, TextContent
from pathlib import Path
import asyncio
from dotenv import load_dotenv

load_dotenv()


token = os.getenv("API_TOKEN")
# Initialise FastMCP server
mcp = FastMCP("Orion Bot Server")

# Configure logging to record events during execution to track behaviour
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OpenAPIToolExtractor:
    """Extracts and manages tools from OpenAPI spec."""

    def __init__(self, spec_path: str, base_url: str = None):
         """initiailise the openapi tool extractor"""

         self.spec_path = spec_path
         self.base_url = base_url
         self.spec = None
         self.tools = {}
         self.client = httpx.AsyncClient()


    async def load_spec(self) -> Dict[str, Any]:
         #load openapi sec
        try:
            with open(self.spec_path, 'r') as f:
                if self.spec_path.endswith('yaml') or self.spec_path.endswith('.yml'):
                    self.spec = yaml.safe_load(f)
                else:
                    self.spec = json.load(f)

            logger.info(f"Loaded openapi spec from {self.spec_path}")
            return self.spec
        
        except FileNotFoundError:
             logger.error(f"openapi spec file not found: {self.spec_path}")
             raise
        except (json.JSONDecodeError, yaml.YAMLError) as e:
             logger.error(f"Invalid openapi spec format: {e}")
             raise 
        
    def _get_base_url(self) -> str:
        """Get the base URL for API calls."""
        if self.base_url:
            return self.base_url
            
        # Try to get from OpenAPI spec servers
        if self.spec and 'servers' in self.spec and self.spec['servers']:
            return self.spec['servers'][0]['url']
            
        return "http://localhost:8000"  # Default fallback
    
    def _convert_openapi_type_to_json_schema(self, param: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAPI parameter to JSON schema format."""
        schema = {}
        
        if 'type' in param:
            schema['type'] = param['type']
        if 'description' in param:
            schema['description'] = param['description']
        if 'enum' in param:
            schema['enum'] = param['enum']
        if 'default' in param:
            schema['default'] = param['default']
        if 'format' in param:
            schema['format'] = param['format']
        if 'minimum' in param:
            schema['minimum'] = param['minimum']
        if 'maximum' in param:
            schema['maximum'] = param['maximum']
        if 'items' in param:
            schema['items'] = self._convert_openapi_type_to_json_schema(param['items'])
            
        return schema
    
    def _extract_parameters(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parameters from OpenAPI operation and convert to JSON schema."""
        properties = {}
        required = []
        
        # Handle parameters (query, path, header)
        for param in operation.get('parameters', []):
            param_name = param['name']
            properties[param_name] = self._convert_openapi_type_to_json_schema(param.get('schema', param))
            
            if param.get('required', False):
                required.append(param_name)
        
        # Handle request body
        if 'requestBody' in operation:
            request_body = operation['requestBody']
            content = request_body.get('content', {})
            
            # Look for JSON content
            if 'application/json' in content:
                json_schema = content['application/json'].get('schema', {})
                if 'properties' in json_schema:
                    properties.update(json_schema['properties'])
                    if 'required' in json_schema:
                        required.extend(json_schema['required'])
            
            # If required request body, mark it as such
            if request_body.get('required', False) and not required:
                required = list(properties.keys())
        
        return {
            'type': 'object',
            'properties': properties,
            'required': required
        }
    
    async def extract_tools(self) -> List[Tool]:
        """Extract tools from OpenAPI specification."""
        if not self.spec:
            await self.load_spec()
        
        tools = []
        
        for path, path_item in self.spec.get('paths', {}).items():
            for method, operation in path_item.items():
                if method.upper() not in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']:
                    continue
                
                # Create tool name from operationId or path+method
                tool_name = operation.get('operationId', f"{method}_{path.replace('/', '_').replace('{', '').replace('}', '')}")
                tool_name = tool_name.replace('-', '_').lower()
                
                # Get description
                description = operation.get('summary', operation.get('description', f"{method.upper()} {path}"))
                
                # Extract parameters
                input_schema = self._extract_parameters(operation)
                
                # Store operation details for execution
                self.tools[tool_name] = {
                    'path': path,
                    'method': method.upper(),
                    'operation': operation,
                    'input_schema': input_schema
                }
                
                # Create MCP tool
                tool = Tool(
                    name=tool_name,
                    description=description,
                    inputSchema=input_schema
                )
                
                tools.append(tool)
                logger.info(f"Extracted tool: {tool_name}")
        
        return tools
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by making HTTP request to the API."""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        tool_info = self.tools[tool_name]
        path = tool_info['path']
        method = tool_info['method']
        operation = tool_info['operation']
        
        # Build URL
        base_url = self._get_base_url().rstrip('/')
        url = f"{base_url}{path}"
        
        # Prepare request parameters
        params = {}
        headers = {"Authorization": f'Bearer {token}'}
        json_data = None
        
        # Handle path parameters
        for param_name, param_value in arguments.items():
            if f"{{{param_name}}}" in url:
                url = url.replace(f"{{{param_name}}}", str(param_value))
        
        # Handle other parameters based on OpenAPI spec
        for param in operation.get('parameters', []):
            param_name = param['name']
            if param_name in arguments:
                param_in = param.get('in', 'query')
                
                if param_in == 'query':
                    params[param_name] = arguments[param_name]
                elif param_in == 'header':
                    headers[param_name] = str(arguments[param_name])
        
        # Handle request body
        if 'requestBody' in operation:
            remaining_args = {k: v for k, v in arguments.items() 
                            if not any(p['name'] == k for p in operation.get('parameters', []))}
            if remaining_args:
                json_data = remaining_args
        
        try:
            # Make HTTP request
            response = await self.client.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                json=json_data
            )
            
            response.raise_for_status()
            
            # Try to parse JSON response
            try:
                result = response.json()
            except:
                result = {"content": response.text, "status_code": response.status_code}
            
            return {
                "success": True,
                "data": result,
                "status_code": response.status_code
            }
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error executing tool {tool_name}: {e}")
            return {
                "success": False,
                "error": str(e),
                "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            }
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def cleanup(self):
        """Cleanup resources."""
        await self.client.aclose()


# Global extractor instance
extractor: Optional[OpenAPIToolExtractor] = None


def load_config(file_path: str) -> Dict[str, Any]:
    """Load server configuration from JSON file.
    
    Args:
        file_path: Path to the JSON configuration file.
        
    Returns:
        Dict containing server configuration.
        
    Raises:
        FileNotFoundError: If configuration file doesn't exist.
        JSONDecodeError: If configuration file is invalid JSON.
    """
    with open(file_path, 'r') as f:
        return json.load(f)


async def initialize_tools_from_openapi(spec_path: str, base_url: str = None):
    """Initialize tools from OpenAPI specification."""
    global extractor
    
    try:
        extractor = OpenAPIToolExtractor(spec_path, base_url)
        tools = await extractor.extract_tools()
        
        # Register each tool with the MCP server
        for tool_info in extractor.tools.values():
            tool_name = None
            for name, info in extractor.tools.items():
                if info == tool_info:
                    tool_name = name
                    break
            
            if tool_name:
                # Create a closure to capture the tool_name
                def make_tool_handler(name):
                    @mcp.tool(name=name, description=tool_info['operation'].get('summary', f"Execute {name}"))
                    async def tool_handler(**kwargs) -> List[TextContent]:
                        result = await extractor.execute_tool(name, kwargs)
                        return [TextContent(type="text", text=json.dumps(result, indent=2))]
                    return tool_handler
                
                # Register the tool handler
                handler = make_tool_handler(tool_name)
                handler()
                
        logger.info(f"Registered {len(tools)} tools from OpenAPI spec")
        
    except Exception as e:
        logger.error(f"Failed to initialize tools from OpenAPI spec: {e}")
        raise



if __name__ == "__main__":
    # Check for OpenAPI spec file
    openapi_spec_path = os.getenv("OPENAPI_SPEC_PATH")
    base_url = os.getenv("API_BASE_URL")
    
    # Initialize tools from OpenAPI spec if file exists
    if os.path.exists(openapi_spec_path):
        asyncio.run(initialize_tools_from_openapi(openapi_spec_path, base_url))
    else:
        logger.warning(f"OpenAPI spec file not found: {openapi_spec_path}")
    
    # Run the server
    try:
        mcp.run(transport='stdio')
    finally:
        try:
        # Cleanup
            if extractor:
                asyncio.run(extractor.cleanup())
        except Exception as e:
            logger.warning(f"Clean up failed: {e}")










