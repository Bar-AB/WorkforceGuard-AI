from mcp_server.server import create_server
from mcp_server.settings import McpSettings

create_server(McpSettings()).run()
