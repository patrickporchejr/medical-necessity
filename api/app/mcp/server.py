from mcp.server.fastmcp import FastMCP

from app.mcp.tools import hello

mcp = FastMCP("medical-necessity-fhir", host="0.0.0.0", port=8001)
mcp.tool()(hello)

if __name__ == "__main__":
    print("hello world")
    mcp.run(transport="streamable-http")
