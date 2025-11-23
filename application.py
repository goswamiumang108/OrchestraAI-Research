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
	"ℹ️ About",
	"📄 SRS Report",
    "🧭 Research Assistant",
    "📚 Research History",
    "⚙️ Settings & Preferences",
    "👤 User Profile"
], index=0, key="main_nav")

# Normalize for existing downstream comparisons (strip emoji)
tab = tab.split(" ", 1)[1] if " " in tab else tab

# ---- Main Content ----
if tab == "About":
	st.markdown(open("README.md", "r", encoding="utf-8").read())

elif tab == "SRS Report":
	st.markdown(open("SRS_Report.md", "r", encoding="utf-8").read())

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