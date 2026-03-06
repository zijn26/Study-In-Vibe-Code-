import os
import json
import logging
from typing import List, Dict, Any

from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

logger = logging.getLogger(__name__)

def get_brightdata_mcp_url() -> str:
    """Helper to get url, can be updated via settings."""
    # Try to load from environment first
    from config import load_dotenv, os
    mcp_url = os.environ.get("BRIGHTDATA_MCP_URL", "").strip()
    api_key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    
    # If both are provided, combine them intelligently
    if mcp_url and api_key:
        if "token=" not in mcp_url:
            # Add token if it's not already in the URL
            separator = "&" if "?" in mcp_url else "?"
            return f"{mcp_url}{separator}token={api_key}"
        else:
            return mcp_url # URL probably already has the token user wants
    elif mcp_url:
        return mcp_url
    elif api_key:
        return f"https://mcp.brightdata.com/sse?token={api_key}&groups=advanced_scraping"
    
    return ""

async def get_mcp_tools() -> List[Dict[str, Any]]:
    """
    Fetch the list of tools from the BrightData MCP Server via SSE.
    """
    url = get_brightdata_mcp_url()
    if not url:
        return []
    
    try:
        async with sse_client(url, timeout=300.0, sse_read_timeout=300.0) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                response = await session.list_tools()
                
                # Convert the tools to OpenAI schema format
                tools = []
                for tool in response.tools:
                    # In mcp python sdk, tool has name, description, inputSchema
                    tool_dict = {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema if hasattr(tool, 'inputSchema') else {}
                        }
                    }
                    tools.append(tool_dict)
                return tools
    except Exception as e:
        logger.error(f"Failed to fetch tools from MCP server: {e}")
        return []

async def execute_mcp_tool(tool_name: str, arguments: dict) -> str:
    """
    Execute a tool on the BrightData MCP Server.
    """
    url = get_brightdata_mcp_url()
    if not url:
        return "Error: BRIGHTDATA_MCP_URL is not configured."
    print("Bat dau tim web ")
    try:
        async with sse_client(url, timeout=300.0, sse_read_timeout=300.0) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                logger.info(f"Executing MCP tool {tool_name} with args: {arguments}")
                result = await session.call_tool(tool_name, arguments)
                
                if result.isError:
                    print("Error executing tool")
                    return f"Error executing tool '{tool_name}'"

                print("Ket qua tim kiem web:", result)
                    
                # Extract text content from result
                texts = []
                for content in result.content:
                    if content.type == "text":
                        texts.append(content.text)
                return "\n".join(texts)
    except Exception as e:
        logger.error(f"Failed to execute MCP tool {tool_name}: {e}")
        return f"Error executing tool {tool_name} via MCP: {str(e)}"
