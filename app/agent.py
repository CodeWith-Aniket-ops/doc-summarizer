# ruff: noqa
import os
import re
import json
from google.adk.agents import Agent
from google.adk.workflow import Workflow, node, START, Edge
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.tools import AgentTool
from google.adk.apps import App, ResumabilityConfig
from google.genai import types

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from .config import config

# ==========================================
# MCP Server Configuration
# ==========================================

mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "-m", "app.mcp_server"],
        )
    )
)

# ==========================================
# Specialized Sub-Agents
# ==========================================

summarizer_agent = Agent(
    name="summarizer_agent",
    model=config.model,
    instruction="""You are the Document Summarizer Agent.
Your job is to read the provided document content and generate high-quality summaries, key points, or action items based on the user's specific request.

Here is the document content:
{document_content}

Depending on what the user asks, you can generate:
- A short summary (1 paragraph)
- A detailed summary (multiple paragraphs)
- Key points (bullet points)
- Action items and important deadlines

Always be accurate and focus only on information present in the document.
You also have access to MCP tools to fetch remote webpages, read document files, extract text metadata, or save generated summaries to files in the workspace. Use these tools when appropriate or when requested.
""",
    description="Generates short/detailed summaries, key points, or action items from the document.",
    tools=[mcp_toolset]
)

qa_expert = Agent(
    name="qa_expert",
    model=config.model,
    instruction="""You are the Q&A Expert Agent.
Your job is to answer the user's questions about the document content accurately, based ONLY on the provided text.

Here is the document content:
{document_content}

Provide clear, structured, and beginner-friendly answers. If the answer cannot be found in the document, politely state that the information is not available in the provided text.
You also have access to MCP tools to read files or fetch webpage content to retrieve information. Use them if needed.
""",
    description="Answers questions about the document content and its summary.",
    tools=[mcp_toolset]
)

# ==========================================
# Orchestrator / Coordinator Agent
# ==========================================

orchestrator_agent = Agent(
    name="orchestrator_agent",
    model=config.model,
    instruction="""You are the Document Summarization Coordinator.
You help users summarize long documents, articles, meeting notes, or reports, and answer questions about them.

You have access to two specialized sub-agents:
1. `summarizer_agent`: For generating summaries. Use this when the user asks for a summary, key points, action items, or bullet points.
2. `qa_expert`: For answering questions. Use this when the user asks a question about the document or its summary.

When delegating, pass the user request verbatim to the appropriate agent.
The document content is: {document_content}

Always communicate in a helpful, conversational, and user-friendly manner.
""",
    tools=[AgentTool(summarizer_agent), AgentTool(qa_expert)]
)

# ==========================================
# Workflow Nodes
# ==========================================

@node
def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    # Initialize document_content to prevent KeyError on formatting
    if "document_content" not in ctx.state:
        ctx.state["document_content"] = ""

    user_message = ""
    if node_input and node_input.parts:
        user_message = "".join([part.text for part in node_input.parts if part.text])
        
    # Check for PII (Email address pattern)
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    pii_found = False
    if re.search(email_pattern, user_message):
        pii_found = True
        for part in node_input.parts:
            if part.text:
                part.text = re.sub(email_pattern, "[REDACTED EMAIL]", part.text)
        user_message = re.sub(email_pattern, "[REDACTED EMAIL]", user_message)
                
    # Check for injection keywords
    injection_keywords = ["ignore previous instructions", "system prompt", "override rules"]
    for kw in injection_keywords:
        if kw in user_message.lower():
            # Log security event in state
            ctx.state["security_violation"] = f"Detected injection keyword: '{kw}'"
            return Event(route="fail", output=user_message)
            
    # Domain-specific rule: confidentiality check
    confidential_keywords = ["confidential", "internal only", "proprietary"]
    has_confidential_terms = any(kw in user_message.lower() for kw in confidential_keywords)
    
    if has_confidential_terms:
        audit_log = {
            "severity": "WARNING",
            "event": "CONFIDENTIALITY_MARKER_DETECTED",
            "details": "Document contains sensitive terms (confidential/proprietary/internal only)"
        }
        print(f"AUDIT LOG: {json.dumps(audit_log)}")
    else:
        audit_log = {
            "severity": "INFO",
            "event": "INPUT_VALIDATION_PASSED",
            "details": f"Input checked. PII scrubbed: {pii_found}."
        }
        print(f"AUDIT LOG: {json.dumps(audit_log)}")
            
    return Event(route="pass", output=node_input)

@node
def security_event(ctx: Context, node_input: str) -> Event:
    message = "⚠️ SECURITY VIOLATION: The input has been flagged for containing prompt injection attempts."
    # Log the event
    audit_log = {
        "severity": "CRITICAL",
        "event": "PROMPT_INJECTION",
        "details": ctx.state.get("security_violation")
    }
    print(f"AUDIT LOG: {json.dumps(audit_log)}")
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=message)]))
    yield Event(output=message)

@node
async def document_ingestion(ctx: Context, node_input: types.Content) -> Event:
    doc_content = ctx.state.get("document_content", "")
    
    user_message = ""
    if node_input and node_input.parts:
        user_message = "".join([part.text for part in node_input.parts if part.text])
        
    # Heuristic to detect if the user pasted a long document/article
    if user_message and (len(user_message) > 150 or "document" in user_message.lower() or "text:" in user_message.lower()):
        doc_content = user_message
        
    # Check resume inputs for document
    if not doc_content:
        if ctx.resume_inputs and "ask_document" in ctx.resume_inputs:
            doc_content = ctx.resume_inputs["ask_document"]
        else:
            yield RequestInput(
                interrupt_id="ask_document",
                message="It looks like you haven't provided a document yet. Please paste the document content or article here to begin."
            )
            return
            
    yield Event(output=node_input, state={"document_content": doc_content})

# ==========================================
# Workflow Definition
# ==========================================

root_agent = Workflow(
    name="doc_summarizer_workflow",
    edges=[
        Edge(from_node=START, to_node=security_checkpoint),
        Edge(from_node=security_checkpoint, to_node=security_event, route='fail'),
        Edge(from_node=security_checkpoint, to_node=document_ingestion, route='pass'),
        Edge(from_node=document_ingestion, to_node=orchestrator_agent),
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
