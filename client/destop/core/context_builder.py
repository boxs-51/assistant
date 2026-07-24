from typing import List
from src.gateway.schemas.message import GatewayMessage, MessageContentPart
from src.gateway.schemas.attachment import TextContent
from client.destop.schemas.context import AgentContextSession

class ContextBuilder:
    @staticmethod
    def build_final_messages(session: AgentContextSession, constitution: str) -> List[GatewayMessage]:
        final_messages = []

        # 1. Build System Prompt
        system_prompt = f"""# HIẾN PHÁP AGENT
{constitution}

"""
        # Inject Topic Context nếu có
        topic = session.scratchpad.get("current_topic")
        goal = session.scratchpad.get("current_goal")
        if topic:
            system_prompt += f"# CURRENT TOPIC & GOAL\n- **Topic**: {topic}\n"
            if goal:
                system_prompt += f"- **Goal**: {goal}\n"
            system_prompt += "\n"

        # Inject Scratchpad & Long-term Memories
        system_prompt += "# BỘ NHỚ HOẠT ĐỘNG (ACTIVE SCRATCHPAD & MEMORIES)\n"
        if session.scratchpad:
            system_prompt += "## Current Scratchpad Variables:\n"
            for k, v in session.scratchpad.items():
                if k in ["current_topic", "current_goal"]: 
                    continue # Bỏ qua vì đã render ở trên
                system_prompt += f"- **{k}**: {v}\n"

        if session.retrieved_memories:
            system_prompt += "\n## Relevant Long-term Memories:\n"
            for mem in session.retrieved_memories:
                system_prompt += f"- [{mem.source_type}] {mem.content}\n"

        system_msg = GatewayMessage(
            role="system",
            content=[MessageContentPart(type="text", data=TextContent(data=system_prompt, format="plain"))]
        )
        final_messages.append(system_msg)

        # 2. Append Lịch sử hội thoại
        final_messages.extend(session.working_messages)

        return final_messages