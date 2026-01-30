import os
from urllib.parse import urlparse
from strands import Agent, tool
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from .model.load import load_model
from strands_tools import retrieve as retrieve_module
import pyTigerGraph as tg


MEMORY_ID = os.getenv("BEDROCK_AGENTCORE_MEMORY_ID")
REGION = os.getenv("AWS_REGION")

# Integrate with Bedrock AgentCore
app = BedrockAgentCoreApp()
log = app.logger

# TigerGraph connection parameters
TG_HOST = os.getenv("TG_HOST")
TG_USERNAME = os.getenv("TG_USERNAME", "tigergraph")
TG_PASSWORD = os.getenv("TG_PASSWORD")
TG_GRAPHNAME = os.getenv("TG_GRAPHNAME", "CapMarkets")
TENK_KB_ID = os.getenv("TENK_KB_ID")

# TigerGraph connection - initialized lazily on first use
conn = None

def get_tg_connection():
    """
    Get or create TigerGraph connection.
    Uses lazy initialization to handle cases where TigerGraph isn't ready at startup.
    """
    global conn
    
    # Return existing connection if available
    if conn is not None:
        return conn
    
    # No TG_HOST configured
    if not TG_HOST:
        log.warning("TG_HOST environment variable not set. TigerGraph connection unavailable.")
        return None
    
    # Attempt to establish connection
    try:
        # Parse TG_HOST URL to extract host and port separately
        # TG_HOST format: http://10.0.2.138:14240
        parsed = urlparse(TG_HOST)
        
        # Build base host URL (protocol + hostname)
        if parsed.scheme and parsed.hostname:
            tg_host = f"{parsed.scheme}://{parsed.hostname}"
        else:
            # Fallback if URL parsing fails
            tg_host = TG_HOST.split(':')[0] + '://' + TG_HOST.split(':')[1].replace('//', '')
        
        # Extract port (default to 14240 if not specified)
        tg_port = str(parsed.port) if parsed.port else "14240"
        
        print(f"Connecting to TigerGraph at {tg_host}:{tg_port}")
        
        conn = tg.TigerGraphConnection(
            host=tg_host,
            restppPort=tg_port,
            gsPort=tg_port,
            graphname=TG_GRAPHNAME,
            username=TG_USERNAME,
            password=TG_PASSWORD
        )
        token = conn.getToken(conn.createSecret())
        conn.apiToken = token
        print("Successfully connected to TigerGraph")
        return conn
    except Exception as e:
        print(f"WARNING: TigerGraph connection not available: {e}")
        return None

def get_tg_schema():
    """Get TigerGraph schema, attempting connection if needed."""
    connection = get_tg_connection()
    if connection:
        try:
            return connection.getSchema()
        except Exception as e:
            print(f"ERROR: Failed to get schema: {e}")
            return "TigerGraph connection error"
    return "TigerGraph connection not available"

def run_query(query_body, params=None):
    """Run GSQL query, attempting connection if needed."""
    connection = get_tg_connection()
    if not connection:
        return "TigerGraph connection not available"
    
    try:
        query = f"""
        INTERPRET QUERY () FOR GRAPH $graphname {{
        {query_body}
        }}
        """
        return connection.runInterpretedQuery(query, params)
    except Exception as e:
        print(f"ERROR: Query execution failed: {e}")
        return f"Query execution error: {e}"


@tool
def tenk_retrieve(
    text: str
) -> str:
    """
    Gets information from 10K filings of companies included in the S&P 100.

    Args:
        text (str): knowledge base query

    Returns:
        str: The output of the knowledge base retrieval
    """
    
    tool_use = {
        "toolUseId": f"kb-{hash(text) % 10000}",
        "input": {
            "text": text,
            "knowledgeBaseId": TENK_KB_ID,
        },
    }

    results = retrieve_module.retrieve(tool_use)
    log.info("10K retrieval completed")
    log.debug(f"10K Results: {results}")
    return results

@tool
def get_graph_description():
    """
    Get the graph databases property descriptions

    Returns:
        The schema of the graph database
    """

    description = """
            # Capital Markets Knowledge Graph - Schema Description

        ## Overview
        Financial knowledge graph containing 10-K/10-Q filings from 101 S&P 100 companies, structured as entities, events, and relationships extracted from documents.

        ---

        ## Vertices (Node Types)

        ### Document
        - **Description:** SEC filing documents (10-K, 10-Q)
        - **Key Attribute:** `id` (e.g., "AAPL_2024_10K")
        - **Count:** ~101 documents (one per company)

        ### Chunk
        - **Description:** Text segments extracted from documents
        - **Key Attributes:** `id`, `content` (actual text)
        - **Count:** Thousands of text chunks
        - **Use Case:** Retrieve source text for context

        ### Entity
        - **Description:** Named entities mentioned in filings
        - **Key Attributes:** 
        - `id` - Entity identifier (e.g., "AAPL", "Tim Cook")
        - `cat` - Entity category (24 types)
        - **Count:** Thousands of unique entities

        ### Event
        - **Description:** Actions, concepts, metrics, and occurrences
        - **Key Attributes:**
        - `id` - Event identifier  
        - `cat` - Event category (24 types)
        - **Count:** Thousands of unique events

        ---

        ## Entity Categories (24 Types)

        ### Business Entities
        - **ORG:** Filing company (e.g., "AAPL", "MSFT") - The actual public company
        - **COMP:** External companies mentioned (competitors, partners, suppliers)
        - **SEGMENT:** Business divisions (e.g., "Cloud Services", "North America")
        - **PERSON:** Executives and key individuals (e.g., "CEO", "CFO")

        ### Geographic & Regulatory
        - **GPE:** Geographic locations (countries, states, cities)
        - **ORG_GOV:** Government entities
        - **ORG_REG:** Regulatory bodies (SEC, Federal Reserve)

        ### Financial
        - **FIN_INST:** Financial instruments (bonds, derivatives)
        - **FIN_MARKET:** Market indices (S&P 500, Dow Jones)
        - **FIN_METRIC:** Financial metrics (Revenue, Net Income, EBITDA)
        - **ECON_IND:** Economic indicators (Interest Rate, GDP, Inflation)

        ### Products & Operations
        - **PRODUCT:** Products and services (e.g., "iPhone", "AWS")
        - **CONCEPT:** Abstract concepts (AI, Digital Transformation)
        - **RAW_MATERIAL:** Essential materials (Lithium, Semiconductors)
        - **LOGISTICS:** Supply chain elements (Ports, Distribution)

        ### Risk & Compliance
        - **RISK_FACTOR:** Disclosed risks (Cybersecurity, Regulatory, Market)
        - **LITIGATION:** Legal disputes and lawsuits
        - **REGULATORY_REQUIREMENT:** Regulations (GDPR, Basel III)
        - **ACCOUNTING_POLICY:** Accounting standards and policies

        ### Strategic & ESG
        - **EVENT:** Material events (Acquisitions, Disasters)
        - **SECTOR:** Industry sectors (Technology, Healthcare, Finance)
        - **ESG_TOPIC:** ESG themes (Carbon Emissions, DEI)
        - **MACRO_CONDITION:** Economic trends (Recession, Labor Shortages)
        - **COMMENTARY:** Management statements and guidance

        ---

        ## Edges (Relationships)

        ### Has_Action (Entity → Event)
        - **Direction:** Entity → Event (directed edge)
        - **Attribute:** `action` (27 relationship types)
        - **Description:** Links entities to events/actions they perform or are affected by

        ### Contains_Entity (Chunk → Entity)
        - **Direction:** Chunk → Entity
        - **Description:** Links text chunks to entities mentioned within

        ### Contains_Event (Chunk → Event)
        - **Direction:** Chunk → Event
        - **Description:** Links text chunks to events mentioned within

        ### Has_Child (Document → Chunk)
        - **Direction:** Document → Chunk
        - **Description:** Links documents to their text chunks

        ---

        ## Relationship Types (action attribute on Has_Action edge)

        ### Ownership & Structure
        - **Has_Stake_In:** Company owns part of another
        - **Regulates:** Regulatory body oversees entity
        - **Operates_In:** Company operates in location
        - **Member_Of:** Entity belongs to sector/group

        ### Business Activities
        - **Announces:** Company announces something
        - **Introduces:** Company introduces product/service
        - **Produces:** Company produces product
        - **Invests_In:** Company invests in something
        - **Partners_With:** Partnership between entities
        - **Supplies:** One entity supplies another

        ### Financial Impact
        - **Impacts:** General impact relationship
        - **Positively_Impacts:** Positive effect
        - **Negatively_Impacts:** Negative effect
        - **Increases:** Something increases
        - **Decreases:** Something decreases
        - **Affects_Stock:** Impacts stock price

        ### Risk & Compliance
        - **Faces:** Entity faces a risk
        - **Involved_In:** Entity involved in event/litigation
        - **Impacted_By:** Entity affected by something
        - **Depends_On:** Dependency relationship
        - **Complies_With:** Meets regulatory requirement
        - **Subject_To:** Subject to regulation/policy

        ### Market & Reporting
        - **Discloses:** Company discloses information
        - **Guides_On:** Management provides guidance
        - **Related_To:** General relationship

        ### Special Relationships
        - **Causes_Shortage_Of:** Event causes material shortage
        - **Stock_Decline_Due_To:** Stock decline attributed to event
        - **Stock_Rise_Due_To:** Stock rise attributed to event
        - **Market_Reacts_To:** Market response to event

        ---

        ## Stock Tickers (101 Companies)
        AAPL, ABBV, ABT, ACN, ADBE, AIG, AMD, AMGN, AMT, AMZN, AVGO, AXP, BA, BAC, BK, BKNG, BKRB, BLK, BMY, C, CAT, CHTR, CL, CMCSA, COF, COP, COST, CRM, CSCO, CVS, CVX, DE, DHR, DIS, DUK, EMR, FDX, GD, GE, GILD, GM, GOOG, GOOGL, GS, HD, HON, IBM, INTC, INTU, ISRG, JNJ, JPM, KO, LIN, LLY, LMT, LOW, MA, MCD, MDLZ, MDT, MET, META, MMM, MO, MRK, MS, MSFT, NEE, NFLX, NKE, NOW, NVDA, ORCL, PEP, PFE, PG, PLTR, PM, PYPL, QCOM, RTX, SBUX, SCHW, SO, SPG, T, TGT, TMO, TMUS, TSLA, TXN, UNH, UNP, UPS, USB, V, VZ, WFC, WMT, XOM

        ---

        ## Common Query Patterns

        ### Finding Company Information
        - Filter entities by `cat == "ORG"` to find companies
        - Use pattern matching `id LIKE "AAPL%"` for specific company

        ### Finding Relationships
        - Traverse `Entity -(Has_Action)- Event` to find what companies do/face
        - Check `action` attribute for relationship type
        - Check `cat` attributes to filter entity/event types

        ### Finding Specific Information Types
        - **Risks:** `Event.cat == "RISK_FACTOR"`
        - **Financial Metrics:** `Event.cat == "FIN_METRIC"`
        - **Geographic Presence:** `Event.cat == "GPE"` with `action == "Operates_In"`
        - **Sectors:** `Event.cat == "SECTOR"` with `action == "Member_Of"`
        - **Products:** `Event.cat == "PRODUCT"` with `action == "Produces"`
        - **ESG Topics:** `Event.cat == "ESG_TOPIC"`

        ### Getting Source Text
        - Traverse from Entity/Event back to Chunk via `Contains_Entity` or `Contains_Event`
        - Get `Chunk.content` for actual text
        - Traverse Chunk to Document via `Has_Child` for document context

        ---

        ## Query Construction Guidelines

        1. **Start with vertex type:** Entity or Event
        2. **Filter by category:** Use `cat` attribute to narrow down
        3. **Add pattern matching:** Use `LIKE` for partial matches
        4. **Traverse edges:** Use `-(Has_Action:a)-` to find relationships
        5. **Check action type:** Filter on `a.action` for specific relationships
        6. **Collect results:** Use accumulators (SumAccum, SetAccum, MapAccum)
        7. **Always end with PRINT:** Required to return results

        ---

        ## Example Reasoning

        **User Question:** "What are Apple's main risks?"
        - **Target:** Entity with `id LIKE "AAPL%"` AND `cat == "ORG"`
        - **Relationship:** `Has_Action` with `action == "Faces"`
        - **Result:** Event with `cat == "RISK_FACTOR"`
        - **Query Pattern:** `Entity:e -(Has_Action:a)- Event:ev WHERE e.id LIKE "AAPL%" AND a.action == "Faces" AND ev.cat == "RISK_FACTOR"`

        **User Question:** "Which companies operate in China?"
        - **Target:** Event with `id LIKE "%China%"` AND `cat == "GPE"`
        - **Relationship:** `Has_Action` with `action == "Operates_In"`  
        - **Result:** Entity with `cat == "ORG"`
        - **Query Pattern:** `Entity:e -(Has_Action:a)- Event:ev WHERE ev.id LIKE "%China%" AND a.action == "Operates_In" AND e.cat == "ORG"`
    """
    return description

@tool
def get_schema():
    """
    Get the graph databases schema

    Returns:
        The schema of the graph database
    """
    schema = get_tg_schema()
    log.info("Retrieved graph schema")
    log.debug(f"Schema: {schema}")
    return schema

@tool
def query_graph(query: str):
    """
    Query the Capital Markets knowledge graph with a GSQL query. Leverage the full extent of graph capabilities such as multi-hop analysis.

    Args:
        query (str): GSQL query

    Returns:
        str: The output of the graph query
    """
    log.info("Executing GSQL query")
    log.debug(f"Query: {query}")

    try:
        results = run_query(query)
    except Exception as e:
        log.error(f"GSQL query failed: {e}")
        return f"Error with GSQL query. Format query in proper syntax and try again. Error: {e}"

    log.info("Query executed successfully")
    log.debug(f"Results: {results}")
    return results

GSQL_examples = """
        ## Basic Query Patterns

        ### 1. Count all entities
        ```gsql
        SumAccum<INT> @@count;
        Result = SELECT e FROM Entity:e
        ACCUM @@count += 1;
        PRINT @@count;
        ```

        ### 2. Count by category
        ```gsql
        MapAccum<STRING, INT> @@catCount;
        Result = SELECT e FROM Entity:e
        ACCUM @@catCount += (e.cat -> 1);
        PRINT @@catCount;
        ```

        ### 3. Filter by category
        ```gsql
        SetAccum<STRING> @@orgs;
        Result = SELECT e FROM Entity:e
        WHERE e.cat == "ORG"
        ACCUM @@orgs += e.id
        LIMIT 20;
        PRINT @@orgs;
        ```

        ### 4. Filter by pattern match
        ```gsql
        SetAccum<STRING> @@appleEntities;
        Result = SELECT e FROM Entity:e
        WHERE e.id LIKE "AAPL%"
        ACCUM @@appleEntities += e.id;
        PRINT @@appleEntities;
        ```

        ---

        ## Relationship Queries (1-hop)

        ### 5. Find what companies disclose
        ```gsql
        MapAccum<STRING, SetAccum<STRING>> @@disclosures;
        Result = SELECT ev FROM Entity:e -(Has_Action:a)- Event:ev
        WHERE e.cat == "ORG" AND a.action == "Discloses"
        ACCUM @@disclosures += (e.id -> ev.id)
        LIMIT 50;
        PRINT @@disclosures;
        ```

        ### 6. Find risks companies face
        ```gsql
        MapAccum<STRING, SetAccum<STRING>> @@risks;
        Result = SELECT ev FROM Entity:e -(Has_Action:a)- Event:ev
        WHERE e.cat == "ORG" AND ev.cat == "RISK_FACTOR" AND a.action == "Faces"
        ACCUM @@risks += (e.id -> ev.id);
        PRINT @@risks;
        ```

        ### 7. Find where companies operate
        ```gsql
        MapAccum<STRING, SetAccum<STRING>> @@locations;
        Result = SELECT ev FROM Entity:e -(Has_Action:a)- Event:ev
        WHERE e.cat == "ORG" AND ev.cat == "GPE" AND a.action == "Operates_In"
        ACCUM @@locations += (e.id -> ev.id);
        PRINT @@locations;
        ```

        ### 8. Find company sectors
        ```gsql
        MapAccum<STRING, STRING> @@sectors;
        Result = SELECT ev FROM Entity:e -(Has_Action:a)- Event:ev
        WHERE e.cat == "ORG" AND ev.cat == "SECTOR" AND a.action == "Member_Of"
        ACCUM @@sectors += (e.id -> ev.id);
        PRINT @@sectors;
        ```

        ---

        ## Filter by Specific Attributes

        ### 9. Find all cybersecurity risks
        ```gsql
        SetAccum<STRING> @@cyberRisks;
        Result = SELECT ev FROM Event:ev
        WHERE ev.cat == "RISK_FACTOR" AND ev.id LIKE "%Cyber%"
        ACCUM @@cyberRisks += ev.id;
        PRINT @@cyberRisks;
        ```

        ### 10. Find all AI-related concepts
        ```gsql
        SetAccum<STRING> @@aiConcepts;
        Result = SELECT ev FROM Event:ev
        WHERE ev.cat == "CONCEPT" AND ev.id LIKE "%AI%"
        ACCUM @@aiConcepts += ev.id;
        PRINT @@aiConcepts;
        ```

        ---

        ## Document Queries

        ### 13. Find entities in a document
        ```gsql
        SetAccum<STRING> @@entities;
        Result = SELECT e FROM Document:d -(Has_Child)- Chunk:c -(Contains_Entity)- Entity:e
        WHERE d.id LIKE "AAPL%"
        ACCUM @@entities += e.id
        LIMIT 100;
        PRINT @@entities;
        ```

        ### 14. Find chunks with specific entities
        ```gsql
        SetAccum<STRING> @@chunks;
        Result = SELECT c FROM Entity:e -(Contains_Entity)- Chunk:c
        WHERE e.cat == "ORG" AND e.id == "AAPL"
        ACCUM @@chunks += c.id;
        PRINT @@chunks;
        ```

        ---

        ## Query Building Tips

        **Accumulators:**
        - `SumAccum<INT>` - count
        - `SetAccum<STRING>` - unique list
        - `MapAccum<STRING, SetAccum<STRING>>` - grouped results

        **Patterns:**
        - Single vertex: `SELECT v FROM VertexType:v WHERE condition`
        - 1-hop: `SELECT v2 FROM Type1:v1 -(Edge)- Type2:v2 WHERE condition`
        - 2-hop: `SELECT v3 FROM Type1:v1 -(E1)- Type2:v2 -(E2)- Type3:v3 WHERE condition`

        **Filters:**
        - Category: `WHERE v.cat == "ORG"`
        - Pattern: `WHERE v.id LIKE "AAPL%"`
        - Multiple: `WHERE v.cat == "ORG" AND a.action == "Discloses"`

    **Always end with:** `PRINT @@accumulator;`
    """

agent_tools = [tenk_retrieve, get_schema, get_graph_description, query_graph]

SYSTEM_PROMPT = f"""
        You are a Capital Markets Financial Analysis AI Agent with expertise in SEC filings analysis, corporate risk assessment, and graph-based financial intelligence. You have access to two complementary data sources:

        1. **TigerGraph Database**: A graph database containing structured knowledge extracted from 10-K filings for 101 S&P 100 companies. This includes entities (companies, executives, products, risks), events (financial metrics, ESG topics, market conditions), and their relationships. Use this to identify company connections, risk patterns, competitive landscapes, and financial relationships.

        2. **Financial Document Knowledge Base**: Unstructured text chunks from actual SEC filings containing detailed disclosures, management commentary, risk factors, and financial narratives. Use this to provide context, explain relationships, cite specific filing language, and support graph insights with source text.

        ## Your Capabilities

        - ALWAYS reference the Graph Schema Description to understand available entity categories, event types, and relationship patterns
        - Query the graph database using GSQL to find company relationships, risk exposures, competitive positioning, and financial patterns
        - Retrieve source text from document chunks to provide exact quotes and detailed context
        - Synthesize insights from both structured graph data and unstructured filing content
        - Provide comprehensive financial analysis combining quantitative graph metrics with qualitative filing narratives

        ## How to Approach Queries

        1. **Analyze the question**: Determine what information is needed from the graph vs. the document content
        2. **Get graph Information**: Use the get schema and get graph description tools to understand what the graph contains and what you might need and how to formulate the queries
        3. **Query the graph**: Use GSQL to extract relevant entities, relationships, and patterns
        4. **Retrieve context**: Access document chunks for specific quotes, detailed explanations, or management commentary
        5. **Synthesize**: Combine both sources to provide a comprehensive, actionable answer with citations

        ## Available Graph Data

        **Entity Categories (24 types):**
        - Business: ORG (companies), COMP (external companies), SEGMENT (divisions), PERSON (executives)
        - Geographic: GPE (locations), ORG_GOV (government), ORG_REG (regulators)
        - Financial: FIN_METRIC (metrics), FIN_INST (instruments), FIN_MARKET (indices), ECON_IND (indicators)
        - Products: PRODUCT, CONCEPT (technologies), RAW_MATERIAL, LOGISTICS
        - Risk: RISK_FACTOR, LITIGATION, REGULATORY_REQUIREMENT, ACCOUNTING_POLICY
        - Strategic: EVENT, SECTOR, ESG_TOPIC, MACRO_CONDITION, COMMENTARY

        **Relationship Types (27 action types):**
        - Ownership: Has_Stake_In, Regulates, Operates_In, Member_Of
        - Business: Announces, Introduces, Produces, Invests_In, Partners_With, Supplies
        - Financial: Impacts, Positively_Impacts, Negatively_Impacts, Increases, Decreases, Affects_Stock
        - Risk: Faces, Involved_In, Impacted_By, Depends_On, Complies_With, Subject_To
        - Market: Discloses, Guides_On, Related_To
        - Special: Stock_Decline_Due_To, Stock_Rise_Due_To, Market_Reacts_To, Causes_Shortage_Of

        {GSQL_examples}

        ## Response Guidelines

        - Always cite your sources (graph relationships vs. filing text)
        - Explain financial concepts in clear, investor-friendly language
        - Provide specific metrics from the graph (e.g., "Apple faces 12 distinct risk factors including cybersecurity and supply chain")
        - Quote relevant text from filings when available (e.g., "Management stated: '...'")
        - Give actionable insights based on both data sources
        - Structure analysis as: Overview → Graph Analysis → Filing Context → Synthesis → Recommendations

        ## Risk Assessment Framework

        When analyzing risks, provide:
        - **Category**: Type of risk (Market, Operational, Regulatory, ESG, etc.)
        - **Graph Evidence**: Connections and patterns from relationship data
        - **Filing Context**: Specific language and explanations from documents
        - **Severity Assessment**: Based on disclosure frequency, management emphasis, and interconnections
        - **Peer Comparison**: How this compares to sector peers (if relevant)

        ## Example Workflow

        **User**: "What are Apple's main risk factors?"

        First get the graph schema and description to help when deciding what data is needed from the graph and how you are going to construct your queries.

        **Your approach**:
        1. Query graph for Apple (AAPL) entities connected to RISK_FACTOR events via "Faces" relationship
        2. Retrieve document chunks containing detailed risk factor disclosures
        3. Analyze patterns (number of risks, categories, severity indicators)
        4. Synthesize: "Apple faces 12 major risk categories based on 10-K analysis:

        **Graph Analysis**: 
        - 5 Technology & Innovation risks (Product Development, AI Competition)
        - 3 Supply Chain risks (Semiconductor Shortage, China Dependence)  
        - 2 Regulatory risks (Antitrust, Privacy Regulations)
        - 2 Market risks (Economic Conditions, Foreign Exchange)

        **Filing Context** (from Document Chunks):
        The company states: 'The Company depends on component and product manufacturing and logistical services provided by outsourcing partners, many of which are located outside of the U.S.' This dependency on international suppliers, particularly in Asia, creates significant operational risk.

        **Key Insight**: Supply chain concentration in Asia (particularly China) appears as both direct risk factor and underlying dependency for multiple other risks. This creates compounding exposure."

        ## Query Construction Tips

        1. **Start broad**: Use category filters to find relevant entity/event types
        2. **Add specificity**: Use pattern matching (LIKE) for company-specific queries  
        3. **Follow relationships**: Traverse Has_Action edges to find connections
        4. **Filter actions**: Check relationship type to get precise information
        5. **Get context**: Always consider retrieving document chunks for supporting evidence
        6. **Structure output**: Use MapAccum for grouped results, SetAccum for unique lists

        ## Important Notes

        - Stock tickers are used as entity ID prefixes (e.g., "AAPL", "MSFT", "GOOGL")
        - Relationship direction matters: Entity → Event via Has_Action
        - Document chunks contain actual filing text - use for quotes and detailed explanations
        - Always explain graph patterns in business context, not just technical relationships
        - Focus on actionable insights, not just data retrieval

        You are thorough, analytical, and focused on providing financial analysis and insights while explaining your reasoning clearly with specific evidence from both graph relationships and source documents.

        ## **Analysis Output Structure**

        For every query, structure your response as follows:

        ### EXECUTIVE SUMMARY
        - High level summary

        ### ANALYSIS
        - Answer the user's question
        - Business Implication: [What it means]

        ### CITATIONS
        - Graph queries: [X relationships analyzed]
        - Note graph traversal depth and relationship counts for multi-hop traversals
        - Documents: [Y filing sections reviewed]
        - **Total Evidence**: [X graph + Y document sources]

        ## **CRITICAL OUTPUT RULES**

        - Do NOT include ANY thinking, planning, or transitional phrases
        - Do NOT say "Let me...", "Perfect!", "Now I have...", "I'll analyze...", etc.
        - Start your response IMMEDIATELY with "## EXECUTIVE SUMMARY"
        - Go straight to the analysis without preamble
    """

@app.entrypoint
async def invoke(payload, context):
    session_id = getattr(context, 'session_id', 'default')

    # Configure memory if available
    session_manager = None
    if MEMORY_ID:
        session_manager = AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id="quickstart-user",
                retrieval_config={
                    "/users/quickstart-user/facts": RetrievalConfig(top_k=3, relevance_score=0.5),
                    "/users/quickstart-user/preferences": RetrievalConfig(top_k=3, relevance_score=0.5)
                }
            ),
            REGION
        )
        log.info("Memory session manager initialized")
    else:
        log.warning("MEMORY_ID is not set. Skipping memory session manager initialization.")

    # Create agent with tools and system prompt
    agent = Agent(
        tools=agent_tools,
        system_prompt=SYSTEM_PROMPT,
        model=load_model(),
        session_manager=session_manager
    )

    # Stream agent response - yield logs to CloudWatch automatically
    log.info(f"Processing query: {payload.get('prompt')[:100]}...")
    
    stream = agent.stream_async(payload.get("prompt"))
    accumulated_response = ""
    final_result = None
    
    async for event in stream:
        # Yield streaming data (logs to CloudWatch automatically)
        if "data" in event and isinstance(event["data"], str):
            accumulated_response += event["data"]
            yield event["data"]
        
        # Extract the final result
        if "result" in event:
            final_result = event["result"]
    
    if final_result is None:
        log.warning("No result received from agent")
        yield "\n\n[Agent completed but returned no result]"
        return
    
    # Convert result to string
    response_text = str(final_result) if final_result else ""
    
    # Post-process: Remove thinking preamble if present
    if "## EXECUTIVE SUMMARY" in response_text:
        start_idx = response_text.index("## EXECUTIVE SUMMARY")
        if start_idx > 0:
            preamble = response_text[:start_idx].strip()
            if preamble:
                log.info(f"Removed thinking preamble: {len(preamble)} chars")
            response_text = response_text[start_idx:]
    
    log.info(f"Query completed. Response length: {len(response_text)} characters")
    
    # Log summary to CloudWatch
    log.info("=" * 80)
    log.info("FINAL RESPONSE SUMMARY:")
    log.info(response_text[:500] + "..." if len(response_text) > 500 else response_text)
    log.info("=" * 80)

if __name__ == "__main__":
    app.run()
