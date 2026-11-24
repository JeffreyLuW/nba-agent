"use client";

import { FormEvent, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// `tools` = names of the tools the agent called for this answer.
type Message = { role: "user" | "assistant"; text: string; tools?: string[] };

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    const question = input;
    setInput("");
    setLoading(true);
    setMessages((m) => [...m, { role: "user", text: question }, { role: "assistant", text: "" }]);

    // The backend streams Server-Sent Events: lines like `data: {"text": "..."}`.
    let res: Response;
    try {
      res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, conversation_id: "demo" }),
      });
      if (!res.ok || !res.body) throw new Error(`backend answered ${res.status}`);
    } catch (err) {
      // Show the problem instead of a "..." bubble that never fills.
      setMessages((m) => [...m.slice(0, -1), { role: "assistant", text: `⚠️ ${err}` }]);
      setLoading(false);
      return;
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const raw of events) {
        const data = raw.replace(/^data: /, "");
        if (data === "[DONE]") continue;
        const event = JSON.parse(data);
        setMessages((m) => {
          const last = m[m.length - 1];
          if (event.type === "tool_call") {
            // The agent is calling an MCP tool: show it, so you can see the agent "think".
            return [...m.slice(0, -1), { ...last, tools: [...(last.tools ?? []), event.name] }];
          }
          if (event.type === "error") {
            return [...m.slice(0, -1), { ...last, text: `${last.text}\n⚠️ Error: ${event.message}` }];
          }
          if (typeof event.text !== "string") return m; // e.g. tool_result: not shown
          return [...m.slice(0, -1), { ...last, text: last.text + event.text }];
        });
      }
    }
    setLoading(false);
  }

  return (
    <main>
      <h1>🏀 NBA Agent</h1>
      {messages.map((m, i) => (
        <div key={i} className={`msg ${m.role}`}>
          {m.tools && m.tools.length > 0 && (
            <div className="tools">🔧 {m.tools.join(", ")}</div>
          )}
          {m.text || "…"}
        </div>
      ))}
      <form onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about any player, team or game…"
        />
        <button disabled={loading}>Send</button>
      </form>
    </main>
  );
}
