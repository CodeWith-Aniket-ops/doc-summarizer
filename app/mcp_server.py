import sys
import os
import re
import json
import urllib.request
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("doc_summarizer_mcp")

@mcp.tool
def read_document_file(file_path: str) -> str:
    """Reads a local text, markdown, or text-based document file and returns its content.
    
    Args:
        file_path: The absolute or relative path to the file.
        
    Returns:
        The text content of the file.
    """
    print(f"MCP Tool read_document_file called for: {file_path}", file=sys.stderr)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"

@mcp.tool
def fetch_webpage_content(url: str) -> str:
    """Fetches the raw text content of a web page/article from a URL.
    
    Args:
        url: The web URL of the page or article to fetch.
        
    Returns:
        The extracted main text content of the webpage.
    """
    print(f"MCP Tool fetch_webpage_content called for: {url}", file=sys.stderr)
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8')
            
        # Strip HTML tags
        text = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Return first 8000 characters
        return text[:8000]
    except Exception as e:
        return f"Error fetching URL {url}: {str(e)}"

@mcp.tool
def extract_document_metadata(content: str) -> str:
    """Analyzes the document text content and returns metadata such as word count and estimated reading time.
    
    Args:
        content: The text content of the document.
        
    Returns:
        A JSON string containing the word count, character count, and estimated reading time.
    """
    print("MCP Tool extract_document_metadata called", file=sys.stderr)
    word_count = len(content.split())
    char_count = len(content)
    reading_time_min = max(1, round(word_count / 200)) # Assumes 200 words per minute
    
    metadata = {
        "word_count": word_count,
        "character_count": char_count,
        "estimated_reading_time_minutes": reading_time_min
    }
    return json.dumps(metadata, indent=2)

@mcp.tool
def save_summary_to_file(file_name: str, summary_content: str) -> str:
    """Saves the generated summary or notes to a text file in the local workspace.
    
    Args:
        file_name: The name of the file to save (e.g. 'summary.txt').
        summary_content: The text content of the summary.
        
    Returns:
        A success message with the file path.
    """
    print(f"MCP Tool save_summary_to_file called for: {file_name}", file=sys.stderr)
    try:
        # Prevent path traversal: only allow saving to current working directory
        safe_name = os.path.basename(file_name)
        with open(safe_name, "w", encoding="utf-8") as f:
            f.write(summary_content)
        return f"Successfully saved summary to {safe_name}"
    except Exception as e:
        return f"Error saving summary to {file_name}: {str(e)}"

if __name__ == "__main__":
    mcp.run()
