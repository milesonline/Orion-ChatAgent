import React, { useState } from "react";
import "./AppStyles.css";

function App() {
  const [message, setMessage] = useState("");
  const [chatLog, setChatLog] = useState([]);

  async function sendMessage() {
    if (!message.trim()) return;

    setChatLog((prev) => [...prev, { sender: "User", text: message }]);

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const data = await res.json();

      setChatLog((prev) => [...prev, { sender: "Orion", text: data.reply }]);
    } catch (err) {
      setChatLog((prev) => [
        ...prev,
        { sender: "Orion", text: "Error connecting to server." },
      ]);
    }

    setMessage("");
  }

  return (
    <div className="container">
      <h2 className="header">Orion Chat</h2>
      <div className="box">
        {chatLog.map((msg, idx) => (
          <p key={idx}>
            <strong>{msg.sender}:</strong> {msg.text}
          </p>
        ))}
      </div>
      <input
        className="inputbox"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && sendMessage()}
      />
      <button className="buttonstyle" onClick={sendMessage}>
        Send
      </button>
    </div>
  );
}

export default App;
