from src.memory.short_term import ShortTermMemory
from src.memory.long_term import LongTermMemory

class ContextEngine:
    def __init__(self, config):
        self.max_context_tokens = config["memory_settings"]["short_term"].get("max_tokens", 3000)
        self.short_mem = ShortTermMemory(max_tokens=self.max_context_tokens)
        self.long_mem = LongTermMemory(storage_path=config["memory_settings"]["long_term"]["storage_path"])
        
        # [MỚI] Bộ nhớ làm việc: Quản lý đa tác vụ theo Session
        # Định dạng: { session_id: ["Task 1 chưa xong", "Task 2 xen ngang"] }
        self.task_stacks = {}

    def push_task(self, session_id: str, task_description: str):
        """Thêm một tác vụ mới lên đầu ngăn xếp khi bị chuyển đổi ngữ cảnh"""
        if session_id not in self.task_stacks:
            self.task_stacks[session_id] = []
        self.task_stacks[session_id].append(task_description)

    def pop_completed_task(self, session_id: str):
        """Đánh dấu hoàn thành và gỡ bỏ tác vụ trên cùng khỏi ngăn xếp"""
        if session_id in self.task_stacks and self.task_stacks[session_id]:
            self.task_stacks[session_id].pop()

    def get_active_tasks(self, session_id: str) -> str:
        """Lấy danh sách các tác vụ đang treo để nhồi vào System Prompt"""
        tasks = self.task_stacks.get(session_id, [])
        if not tasks:
            return "Không có tác vụ nào đang treo. Hãy chờ lệnh mới."
        
        # Tác vụ ở cuối mảng là tác vụ đang active nhất (LIFO)
        task_list = "\n".join([f"{i+1}. {task} {'(ĐANG XỬ LÝ)' if i == len(tasks)-1 else '(ĐANG TẠM DỪNG)'}" 
                               for i, task in enumerate(tasks)])
        return task_list

    def build_rich_context(self, session_id: str, user_request: str) -> dict:
        """Lắp ráp bối cảnh toàn diện: Long-term + Short-term + Working Memory"""
        past_facts = self.long_mem.search_relevant_facts(user_request)
        active_tasks = self.get_active_tasks(session_id)
        
        # Tính toán token và cắt tỉa short_mem (giữ nguyên logic đã làm trước đó)
        past_facts_tokens = self.short_mem.count_tokens(past_facts)
        active_tasks_tokens = self.short_mem.count_tokens(active_tasks)
        
        available_short_term_tokens = max(1000, self.max_context_tokens - past_facts_tokens - active_tasks_tokens)
        
        original_max = self.short_mem.max_tokens
        self.short_mem.max_tokens = available_short_term_tokens
        self.short_mem._prune_by_tokens(session_id)
        self.short_mem.max_tokens = original_max
        
        chat_history = self.short_mem.get_context(session_id)
        
        # Kết hợp toàn bộ vào 1 khối bối cảnh duy nhất
        full_prompt_context = (
            f"[TRẠNG THÁI TÁC VỤ (WORKING MEMORY)]:\n{active_tasks}\n\n"
            f"[KÝ ỨC QUÁ KHỨ]:\n{past_facts}\n\n"
            f"[LỊCH SỬ CHAT]:\n{chat_history}"
        )
        
        return {
            "chat_history": chat_history,
            "past_facts": past_facts,
            "active_tasks": active_tasks,
            "full_prompt_context": full_prompt_context
        }

    def update_context(self, session_id: str, user_request: str, ai_response: str):
        self.short_mem.add_message(session_id, "user", user_request)
        self.short_mem.add_message(session_id, "assistant", ai_response)