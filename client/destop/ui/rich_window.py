import customtkinter as ctk
import threading

class AgentUI(ctk.CTk):
    def __init__(self, engine, hitl):
        super().__init__()
        self.engine = engine
        self.hitl = hitl
        
        self.title("Modular Agent - Dynamic Risk HITL System")
        self.geometry("950x700")

        # Đăng ký hàm phê duyệt UI vào HITL Manager
        self.hitl.set_approval_callback(self.show_approval_dialog)

        # UI Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.txt_console = ctk.CTkTextbox(self, font=("Segoe UI", 13), wrap="word")
        self.txt_console.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        # Approval Bar (Mặc định ẩn)
        self.approval_bar = ctk.CTkFrame(self, fg_color="#4a1515") # Màu đỏ cảnh báo
        self.lbl_risk_info = ctk.CTkLabel(self.approval_bar, text="", font=("Segoe UI", 12, "bold"))
        self.lbl_risk_info.pack(side="left", padx=10, pady=5)
        
        self.btn_yes = ctk.CTkButton(self.approval_bar, text="✅ Phê duyệt (Allow)", fg_color="green", width=120, command=lambda: self._respond(True))
        self.btn_yes.pack(side="right", padx=5, pady=5)
        
        self.btn_no = ctk.CTkButton(self.approval_bar, text="❌ Từ chối (Block)", fg_color="red", width=120, command=lambda: self._respond(False))
        self.btn_no.pack(side="right", padx=5, pady=5)

        # Input Frame
        input_frame = ctk.CTkFrame(self)
        input_frame.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
        input_frame.grid_columnconfigure(0, weight=1)

        self.entry_cmd = ctk.CTkEntry(input_frame, placeholder_text="Nhập lệnh (/init, /tools, /skills, /context hoặc câu hỏi)...")
        self.entry_cmd.grid(row=0, column=0, padx=(0, 10), pady=5, sticky="ew")
        self.entry_cmd.bind("<Return>", lambda e: self.on_send())

        self.btn_send = ctk.CTkButton(input_frame, text="Gửi", command=self.on_send)
        self.btn_send.grid(row=0, column=1)

        self._user_response = None

    def render_block(self, btype: str, text: str):
        colors = {
            "thought": "💭 [THOUGHT]: ",
            "action": "🛠️ [PROPOSED ACTION]: ",
            "observation": "👁️ [OBSERVATION]: ",
            "system": "⚙️ [SYSTEM]: ",
            "error": "❌ [ERROR]: "
        }
        prefix = colors.get(btype, "")
        self.txt_console.insert("end", f"\n{prefix}{text}\n")
        self.txt_console.see("end")

    def show_approval_dialog(self, req_data: dict) -> bool:
        """Hàm ngắt vòng lặp UI để xin ý kiến trực tiếp từ người dùng"""
        bg_color = "#5c3a00" if req_data["risk_level"] == "HIGH" else "#610e0e" # Vàng cho HIGH, Đỏ thẫm cho CRITICAL
        self.approval_bar.configure(fg_color=bg_color)
        self.approval_bar.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        
        self.lbl_risk_info.configure(
            text=f"⚠️ [{req_data['risk_level']}] Yêu cầu duyệt {req_data['type']}: {req_data['name']}('{req_data['args']}')\nLý do: {req_data['reason']}"
        )
        
        self._user_response = None
        # Vòng lặp chờ phản hồi từ nút bấm UI (Non-blocking Threading Safe)
        while self._user_response is None:
            self.update()
            
        self.approval_bar.grid_forget()
        return self._user_response

    def _respond(self, choice: bool):
        self._user_response = choice

    def on_send(self):
        text = self.entry_cmd.get().strip()
        if not text: return
        self.entry_cmd.delete(0, "end")
        
        self.render_block("system", f"USER INPUT: {text}")
        threading.Thread(target=self.engine.step_execute, args=(text, self.render_block), daemon=True).start()