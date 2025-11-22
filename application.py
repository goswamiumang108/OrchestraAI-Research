import asyncio
import json
import os
from datetime import datetime
import nest_asyncio
import streamlit as st

from ODR_Agent.deep_researcher import deep_researcher

# Enable nested event loops for Streamlit reruns / async safety
nest_asyncio.apply()

# Ensure we read API keys from config (user-provided in Settings)
os.environ["GET_API_KEYS_FROM_CONFIG"] = "true"

HISTORY_FILE = "history.json"


# ---------------- Helper Functions ----------------
def get_message_role(msg):
	# Handles both dict and object (e.g., AIMessage) types
	if isinstance(msg, dict):
		return msg.get("role", "assistant")
	return getattr(msg, "role", "assistant")


def get_message_content(msg):
	# Handles both dict and object (e.g., AIMessage) types
	if isinstance(msg, dict):
		return msg.get("content", "")
	return getattr(msg, "content", "")


# ---------------- History Persistence ----------------
def save_history(topic, report):
	history = get_history()
	history.append({"topic": topic, "report": report, "timestamp": datetime.now().isoformat()})
	# Write with utf-8 encoding
	with open(HISTORY_FILE, "w", encoding="utf-8") as f:
		json.dump(history, f, ensure_ascii=False, indent=2)


def get_history():
	# Robustly read history.json; if file is corrupt, back it up and return empty history
	if not os.path.exists(HISTORY_FILE):
		return []
	try:
		with open(HISTORY_FILE, "r", encoding="utf-8") as f:
			return json.load(f)
	except (json.JSONDecodeError, OSError):
		# Attempt to preserve the corrupt file for inspection and start fresh
		try:
			os.rename(HISTORY_FILE, HISTORY_FILE + ".corrupt")
		except Exception:
			# If rename fails, ignore and return empty history
			pass
		return []


# ---------------- Async Invocation Utilities ----------------
def _run_async(coro):
	"""Run an async coroutine safely inside Streamlit.

	Uses existing loop with nest_asyncio if already running, else creates a new loop.
	"""
	try:
		loop = asyncio.get_running_loop()
	except RuntimeError:
		# No running loop -> simple
		return asyncio.run(coro)
	else:
		# Already inside a loop; nest_asyncio allows re-entry
		return loop.run_until_complete(coro)


def build_config_from_settings() -> dict:
	"""Build RunnableConfig.configurable from session settings.

	Returns a dict with a configurable section matching Configuration model field names.
	"""
	settings = st.session_state.get("settings", {})
	provider = settings.get("provider", "Google")
	temperature = settings.get("temperature", 0.2)
	max_tokens = settings.get("max_tokens", 2048)
	
	# Map provider -> model base names used across pipeline
	if provider == "Anthropic":
		model_name = "anthropic:claude-3-5-sonnet"
	else:
		model_name = "gemini-2.0-flash"
	
	api_keys = settings.get("apiKeys", {})
	
	# Build configurable programmatically to avoid repetition
	roles = ["research", "summarization", "compression", "final_report"]
	configurable = {}
	for r in roles:
		# e.g. research_model, research_model_max_tokens
		configurable[f"{r}_model" if r != "research" else "research_model"] = model_name
		configurable[f"{r}_model_max_tokens" if r != "research" else "research_model_max_tokens"] = max_tokens
	
	# Add other knobs
	configurable.update({"allow_clarification": True, "max_researcher_iterations": 6, "max_react_tool_calls": 10,
		"max_concurrent_research_units":        5, "search_api": "tavily", "apiKeys": api_keys,
		"temperature":                          temperature, })
	return {"configurable": configurable}


def run_deep_research_messages(messages: list[dict], allow_clarification: bool | None = None) -> dict:
	"""Invoke deep_researcher graph with an existing conversation message list.

	messages: list of {role, content}
	allow_clarification: optional override for configuration allow_clarification.
	Returns raw graph result dict.
	"""
	config = build_config_from_settings()
	if allow_clarification is not None:
		config.setdefault("configurable", {})["allow_clarification"] = allow_clarification
	
	return _run_async(deep_researcher.ainvoke({"messages": messages}, config))


# Centralized helper to run the researcher and update session & history to avoid duplicate logic
def _process_research_call(messages: list[dict], topic: str | None = None, allow_clarification: bool = True) -> dict:
	"""Call deep_researcher with messages, update session_state and save history when final report is available.

	Returns raw result dict. Does not manipulate `st.session_state['processing']` so caller can manage UI state.
	"""
	config = build_config_from_settings()
	config.setdefault("configurable", {})["allow_clarification"] = allow_clarification
	
	result = _run_async(deep_researcher.ainvoke({"messages": messages}, config))
	
	# Update conversation messages if the graph returned them
	if result.get("messages"):
		st.session_state['conversation_messages'] = result["messages"]
	
	# Capture final report if available and persist
	if result.get("final_report"):
		st.session_state['report'] = result["final_report"]
		# Use provided topic or derive from the first user message
		resolved_topic = topic or (
			get_message_content(st.session_state['conversation_messages'][0]) if st.session_state[
				'conversation_messages'] else "Untitled")
		save_history(resolved_topic, st.session_state['report'])
	
	return result


# ---------------- Session Defaults ----------------
if "settings" not in st.session_state:
	st.session_state["settings"] = {"provider": "Google", "temperature": 0.2, "max_tokens": 2048, "apiKeys": {}, }
if "processing" not in st.session_state:
	st.session_state["processing"] = False

# ---------------- UI Layout ----------------
st.set_page_config(page_title="OrchestraAI Research", layout="wide", initial_sidebar_state="expanded")

# Sidebar header with emoji and compact description for improved look
st.sidebar.markdown("# 🧠 OrchestraAI Research")

# Radio with emojis for visual differentiation
tab = st.sidebar.radio("Navigate to", [
	"📚 About",
    "🧭 Research Assistant",
    "📚 Research History",
    "⚙️ Settings & Preferences",
    "👤 User Profile"
], index=0, key="main_nav")

# Normalize for existing downstream comparisons (strip emoji)
tab = tab.split(" ", 1)[1] if " " in tab else tab

# ---- Main Content ----
if tab == "About":
	st.title("About OrchestraAI Research 🧠")
	st.subheader("The Agent-as-a-Service for Deep Research Orchestration & Knowledge Synthesis")
	
	st.divider()
	
	st.subheader("Abstract")
	st.markdown("""
	This work presents the design and implementation of OrchestraAI Research, an Agent-as-a-Service (AaaS) system for deep research orchestration and knowledge synthesis. The proposed service delivers an autonomous research agent that integrates retrieval-augmented generation (RAG), multi-agent orchestration, and natural conversational interaction to provide users with reliable, evidence-grounded, and structured outputs. Unlike existing systems that specialize either in dialogue (e.g., ChatGPT, Claude) or information retrieval (e.g., Perplexity, NotebookLM), OrchestraAI unifies these capabilities into a single AaaS framework. The system supports both user-provided documents and real-time web research, generating academic-quality outputs such as summaries, reports, citations, and knowledge graphs. By offering this functionality through a service-oriented model, OrchestraAI ensures accessibility for students, researchers, professionals, and general learners, while maintaining academic rigor, scalability, and future extensibility.
	""")
	
	st.subheader("Introduction")
	st.markdown("""
	In today’s information-driven world, individuals and organizations face an unprecedented challenge: navigating the
	overwhelming volume of unstructured digital knowledge spread across academic papers, online platforms, reports, and
	private documents.
	
	Traditional tools for information discovery are fragmented — conversational systems such as ChatGPT, Gemini, and Claude
	excel at interactive dialogue and reasoning but often lack reliable citations, while research-focused platforms like
	Perplexity and Elicit emphasize retrieval and references but provide limited conversational depth.
	
	NotebookLM, developed by Google, demonstrates the utility of document-centric study assistants but does not support
	comprehensive open-web research. These systems illustrate significant progress yet highlight a persistent gap: the
	absence of a unified solution that integrates discovery, dialogue, and synthesis in a reliable, scalable manner.
	
	OrchestraAI Research addresses this gap through an Agent-as-a-Service (AaaS) model. Unlike conventional software
	platforms, the AaaS approach delivers an autonomous research agent as a service, enabling users to offload complex
	research tasks without managing infrastructure.
	
	Built upon state-of-the-art frameworks such as LangChain and LangGraph, and powered by Retrieval-Augmented Generation (
	RAG) with FAISS-based semantic retrieval, the system orchestrates multi-step research pipelines that integrate both
	user-provided documents and real-time web data via APIs like Tavily and Gemini. The outputs are not only conversational
	but also academically rigorous, including structured summaries, detailed reports, and knowledge graphs with citations.
	
	By unifying deep research orchestration, conversational interaction, and structured synthesis in an AaaS model,
	OrchestraAI Research ensures accessibility for students, academics, and professionals alike. It represents a novel
	direction in AI-driven research support — bridging usability, scalability, and academic quality within a single
	autonomous service.
	""")
	
	st.subheader("Problem Domain")
	st.markdown("""
	The exponential growth of digital information has created a paradox: while knowledge is more accessible than ever,
	synthesizing it into reliable, structured insights remains difficult.
	
	Students, researchers, and professionals often rely on fragmented tools — conversational AI systems like ChatGPT,
	Gemini, or Claude provide fluent interaction but lack consistent citation grounding, whereas platforms such as
	Perplexity or Elicit emphasize evidence-backed retrieval but deliver limited conversational depth. Document-based
	systems like NotebookLM demonstrate the value of personalized research, yet remain restricted to closed sources without
	true open-web integration.
	
	The core problem is the absence of an Agent-as-a-Service (AaaS) model that unifies these strengths into a single,
	autonomous solution. Current workflows require users to manually combine multiple systems, resulting in inefficiency,
	redundancy, and incomplete insights.
	
	**Objectives of the Proposed Work**
	- Design an AI-powered research and conversational agent with RAG integration.
	- Enable context-aware dialogue for dynamic exploration of information.
	- Provide structured outputs such as summaries, reports, and Knowledge Graphs.
	- Balance accessibility for general users with rigor for academic and professional use.
	""")
	
	st.subheader("Solution Domain")
	st.markdown("""
	The proposed solution, OrchestraAI Research, is conceptualized as an Agent-as-a-Service (AaaS) system that delivers
	autonomous research capabilities through a web-based interface. By abstracting infrastructure and orchestration
	complexity, users can interact with a research agent that integrates multiple advanced AI techniques to provide
	reliable, structured knowledge synthesis.
	
	At its core, the system leverages Retrieval-Augmented Generation (RAG) to ground responses in evidence from both
	user-uploaded documents and real-time web sources. For semantic search and contextual retrieval, FAISS is employed as a
	high-performance vector database.
	
	To enable multi-step workflows, LangChain and LangGraph are utilized for orchestrating agent pipelines, supporting query
	decomposition, evidence aggregation, and synthesis.
	
	The architecture comprises a modular three-tier design:
	
	1. Frontend (SaaS-style interface): A web-based conversational interface enabling document upload, query input, and
	   export of results as reports or summaries.
	2. Backend Orchestrator: Python-based implementation that coordinates agents, handles RAG workflows, and integrates APIs
	   such as Tavily (for structured web search) and Gemini (for advanced language modeling).
	3. Knowledge Layer: Embedding store (FAISS) for document indexing, coupled with a synthesizer module for generating
	   academic-quality outputs with citations.
	
	From a design perspective, the framework emphasizes extensibility — future versions may incorporate multimodal inputs (
	e.g., audio, video), knowledge graph visualization, and domain-specific agents (legal, medical, or academic).
	
	The plan for implementation follows an incremental development cycle: (i) core RAG pipeline and chat interface; (ii)
	integration of multi-source research workflows via LangGraph; (iii) citation-backed synthesis; and, (iv) testing and
	deployment as a web-accessible AaaS.
	
	By combining state-of-the-art retrieval, orchestration, and synthesis into an AaaS model, OrchestraAI Research offers a
	scalable, user-friendly, and academically rigorous solution to the challenges of modern knowledge exploration.
	""")
	
	st.subheader("System Domain")
	st.markdown("""
	The implementation of OrchestraAI Research as an Agent-as-a-Service (AaaS) requires a combination of robust development frameworks, APIs, and suitable hardware/software environments.

	The system is developed in Python, chosen for its mature ecosystem of AI/ML libraries and seamless integration with LangChain and LangGraph, which provide the orchestration layer for multi-agent research pipelines. FAISS is employed as the vector database for efficient similarity search and retrieval, justified by its proven scalability on commodity hardware.
	
	For external intelligence and up-to-date research capabilities, APIs such as Tavily (structured web search) and Google AI Studio’s Gemini models are integrated, ensuring both breadth and depth in knowledge acquisition. Development is carried out in PyCharm Professional IDE, offering advanced debugging and productivity features.
	
	Development and testing will be carried out on a high-performance personal system with the following specifications:
	
	| Component        | Specification                                      |
	|------------------|----------------------------------------------------|
	| Processor        | 12th Gen Intel® Core™ i7-12650H @ 2.30 GHz         |
	| Installed RAM    | 16 GB (15.7 GB usable)                             |
	| System Type      | 64-bit OS, x64-based processor                     |
	| Graphics         | 4 GB dedicated GPU (multiple GPUs supported)       |
	| OS Environment   | Windows 11 (64-bit)                                |
	| IDE              | PyCharm Professional                               |
	| Database         | FAISS Vector DB                                    |
	| Frameworks/Tools | LangChain, LangGraph, Tavily API, Google AI Studio |
	
	The inclusion of a dedicated GPU is particularly important for accelerating embedding generation, vector similarity search, and LLM-based inference tasks, ensuring reduced latency and efficient execution of RAG pipelines.
	
	Combined with robust CPU and RAM resources, this setup provides a reliable environment for prototyping, testing, and scaling the proposed solution.
		""")
	
	st.subheader("Application Domain")
	st.markdown("""
	The scope of OrchestraAI Research lies in providing Agent-as-a-Service (AaaS) for academic, professional, and industrial research workflows.
	The system can be applied in universities for student projects, literature surveys, and thesis work; in enterprises for market analysis and knowledge management; and in policy or legal domains for evidence-backed exploration. A variant of the system may extend into domain-specific AaaS agents (e.g., medical research assistant, legal research agent, corporate intelligence agent).
	Its real-life impact is significant — by reducing research time, enhancing reliability, and generating structured outputs, it empowers end-users to make faster, evidence-driven decisions with academic rigor.
		""")
	
	st.subheader("Expected Outcome")
	st.markdown("""
	The proposed system is expected to deliver the following outcomes:

	* Deployment of OrchestraAI Research as Agent-as-a-Service (AaaS) accessible via a web interface.
	* Implementation of deep research pipelines using RAG and LangGraph orchestration.
	* FAISS-powered semantic search for efficient document retrieval and contextual grounding.
	* Integration of user-uploaded documents and real-time web research through APIs.
	* Generation of academic-quality outputs: summaries, reports, citations, and knowledge graphs.
	* A synthesizer module for merging multi-source evidence into coherent answers.
	* Demonstrated scalability and extensibility to future domain-specific AaaS agents.
	* Real-world usability: reducing research time, improving trustworthiness, and enhancing productivity.
	""")
	
	st.subheader("References")
	st.markdown("""
	[1] T. B. Brown et al., “Language Models are Few-Shot Learners,” Advances in Neural Information Processing Systems (NeurIPS), vol. 33, pp. 1877–1901, 2020.

	[2] LangChain AI, “Open Deep Research,” GitHub Repository. [Online]. Available: https://github.com/langchain-ai/open_deep_research. [Accessed: Sep. 10, 2025].
	
	[3] LangChain AI, “Deep Research with LangGraph,” LangChain Academy. [Online]. Available: https://academy.langchain.com/courses/deep-research-with-langgraph. [Accessed: Sep. 10, 2025].
	
	[4] Tavily, “Tavily Research API Documentation.” [Online]. Available: https://docs.tavily.com/. [Accessed: Sep. 10, 2025].
	
	[5] Google, “Google AI Studio Documentation.” [Online]. Available: https://ai.google.dev/docs. [Accessed: Sep. 10, 2025].
	
	[6] Meta AI, P. Lewis, E. Perez, A. Piktus, F. Petroni et al., “Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks,” arXiv preprint arXiv:2005.11401, 2020.
	
	[7] Anthropic, “Claude AI — About the Product,” Anthropic. [Online]. Available: https://www.anthropic.com/claude. [Accessed: Sep. 10, 2025].
	
	[8] OpenAI, “ChatGPT: Optimizing Language Models for Dialogue,” OpenAI Blog. [Online]. Available: https://openai.com/blog/chatgpt. [Accessed: Sep. 10, 2025].
	
	[9] Google DeepMind, “Gemini — Multimodal AI Model,” DeepMind. [Online]. Available: https://deepmind.google/technologies/gemini. [Accessed: Sep. 10, 2025].
	
	[10] Google, “NotebookLM — AI-Powered Research Notebook,” Google. [Online]. Available: https://notebooklm.google/. [Accessed: Sep. 10, 2025].
	""")

elif tab == "Research Assistant":
	st.title("OrchestraAI Research 🧠")
	st.subheader("The Agent-as-a-Service for Deep Research Orchestration & Knowledge Synthesis")
	
	# Initialize session state containers
	if 'conversation_messages' not in st.session_state:
		st.session_state['conversation_messages'] = []
	
	if 'report' not in st.session_state:
		st.session_state['report'] = None
	
	topic = st.text_area("Research topic/question", height=100, placeholder="e.g., What are the latest advances in "
	                                                                        "quantum computing?", key="topic_input",
	                                                                        disabled=st.session_state.get('processing', False))
	col1, col2, col3 = st.columns([1, 1, 1])
	with col1:
		run_btn = st.button("Start / Continue Research", key="run_research_btn", disabled=st.session_state.get('processing', False))
	with col2:
		force_continue_btn = st.button("Force Continue (Skip Clarification)", key="force_continue_btn", disabled=st.session_state.get('processing', False))
	with col3:
		stop_btn = st.button("Stop", key="stop_btn", disabled=not st.session_state.get('processing', False))
		if stop_btn:
			st.session_state['stop_requested'] = True
	
	# Start new conversation if empty and user provides topic
	if run_btn and topic.strip():
		st.session_state['processing'] = True
		st.session_state['stop_requested'] = False
		if not st.session_state['conversation_messages']:
			st.session_state['conversation_messages'] = [{"role": "user", "content": topic.strip()}]
		with st.spinner("Processing..."):
			try:
				result = _process_research_call(
					st.session_state['conversation_messages'], topic=topic.strip(), allow_clarification=True)
			finally:
				st.session_state['processing'] = False
	
	# Force continue (skip clarification -> jump to write_research_brief)
	if force_continue_btn and st.session_state['conversation_messages']:
		st.session_state['processing'] = True
		st.session_state['stop_requested'] = False
		with st.spinner("Advancing research (skipping clarification)..."):
			try:
				result = _process_research_call(
					st.session_state['conversation_messages'], topic=(topic or None), allow_clarification=False)
			finally:
				st.session_state['processing'] = False
	
	# Show clarification question if present and no report yet
	if (not st.session_state['report'] and st.session_state['conversation_messages'] and get_message_role(
			st.session_state['conversation_messages'][-1]) in ("assistant", "ai")):
		st.warning("The agent asked for clarification. You can answer below or force continue.")
		
		clarification_answer = st.text_input("Your clarification answer", key="clarification_answer")
		if st.button("Submit Clarification Answer", key="submit_clarification_btn") and clarification_answer.strip():
			st.session_state['conversation_messages'].append({"role": "user", "content": clarification_answer.strip()})
			
			with st.spinner("Re-evaluating..."):
				try:
					result = _process_research_call(
						st.session_state['conversation_messages'], topic=(topic or None), allow_clarification=True)
				except Exception as e:
					st.error(f"Error: {e}")
	
	# Display report if available
	if st.session_state.get('report'):
		st.header("Research Report")
		st.markdown(st.session_state['report'])
		st.download_button("Download Markdown", st.session_state['report'], file_name="report.md")
	else:
		# Show current conversation (for transparency)
		if st.session_state['conversation_messages']:
			st.subheader("Conversation Progress")
			for m in st.session_state['conversation_messages']:
				role = get_message_role(m)
				content = get_message_content(m)
				timestamp = m.get("timestamp") if isinstance(m, dict) else None
				if role == "user":
					with st.chat_message("user", avatar="user"):
						st.markdown(content)
						if timestamp:
							st.caption(f"{timestamp}")
				else:
					with st.chat_message("assistant", avatar="assistant"):
						st.markdown(content)
						if timestamp:
							st.caption(f"{timestamp}")

elif tab == "Research History":
	st.title("Past Research History")
	history = get_history()
	if not history:
		st.info("No research history yet.")
	else:
		for idx, session in enumerate(reversed(history)):
			with st.expander(session["topic"]):
				st.markdown(session["report"])
				st.download_button("Download Markdown",
					session["report"], file_name="report.md", key=f"download_history_{idx}")

elif tab == "Settings & Preferences":
	st.title("Settings & Preferences")
	st.markdown("Configure your research preferences. These will apply to future research runs.")
	st.info("For longer and more detailed reports, increase the 'Max tokens' value below to the highest supported by "
	        "your model.")
	
	current = st.session_state["settings"]
	
	provider = st.selectbox("LLM Provider", ["Google", "Anthropic"], index=["Google",
	                                                                        "Anthropic"].index(current.get("provider", "Google")))
	temperature = st.slider("Temperature", min_value=0.0, max_value=1.0, value=float(current.get("temperature", 0.2)), step=0.05)
	max_tokens = st.number_input("Max tokens (applied to all model stages)", min_value=512, max_value=200000, value=int(current.get("max_tokens", 2048)), step=256)
	
	st.markdown("### API Keys (stored only in session state)")
	api_keys = current.get("apiKeys", {})
	with st.expander("Provide API Keys"):
		tavily_key = st.text_input("Tavily API Key", value=api_keys.get("TAVILY_API_KEY", ""), type="password")
		if provider == "Anthropic":
			anthropic_key = st.text_input("Anthropic API Key", value=api_keys.get("ANTHROPIC_API_KEY", ""), type="password")
		else:
			anthropic_key = api_keys.get("ANTHROPIC_API_KEY", "")
		if provider == "Google":
			google_key = st.text_input("Google API Key", value=api_keys.get("GOOGLE_API_KEY", ""), type="password")
		else:
			google_key = api_keys.get("GOOGLE_API_KEY", "")
	
	if st.button("Save Settings", key="save_settings_btn"):
		st.session_state["settings"] = {"provider": provider, "temperature": temperature, "max_tokens": max_tokens,
		                                "apiKeys":  {
			                                "TAVILY_API_KEY":    tavily_key.strip() or api_keys.get("TAVILY_API_KEY", ""),
			                                "ANTHROPIC_API_KEY": anthropic_key.strip() if provider == "Anthropic" else anthropic_key,
			                                "GOOGLE_API_KEY":    google_key.strip() if provider == "Google" else google_key, }, }
		st.success("Settings saved and will be used in the next run.")
	
	st.code(json.dumps({k: (v if k != 'apiKeys' else {ak: ('***' if av else '') for ak, av in v.items()}) for k, v in
	                    st.session_state['settings'].items()}, indent=2), language="json")
	st.caption("(API key values masked above – only presence is shown.)")

elif tab == "User Profile":
	st.title("User Profile & Usage Stats")
	st.info("User authentication and profile features coming soon!")

else:
	st.error("Tab not implemented.")