import { useState, useRef, useEffect } from 'react'
import './App.css'
import { z } from 'zod';

const PrintText = z.object({
  type: z.literal('print-text'),
  text: z.string(),
});

type PrintText = z.infer<typeof PrintText>;

const ChangeColor = z.object({
  type: z.literal('change-color'),
  color: z.string(),
});
type ChangeColor = z.infer<typeof ChangeColor>;

const Action = z.discriminatedUnion('type', [
  PrintText,
  ChangeColor,
]);
type Action = z.infer<typeof Action>;

const Outcome = z.object({
  actions: z.array(Action),
})
type Outcome = z.infer<typeof Outcome>;



function App() {
  const webSocketRef = useRef<WebSocket | null>(null);

  const [messages, setMessages] = useState<string[]>([]);

  const [draftMessage, setDraftMessage] = useState('');

  const [color, setColor] = useState('black');

  useEffect(() => {
    webSocketRef.current = new WebSocket('ws://localhost:8000/ws');
    webSocketRef.current.onopen = () => {
      console.log('WebSocket connection established');
    };

    const webSocket = webSocketRef.current;
    return () => {
      if (webSocket) {
        webSocket.close();
        console.log('WebSocket connection closed');
      }
    };
  }, []);


  useEffect(() => {
    if (webSocketRef.current) {
      webSocketRef.current.onmessage = (event) => {
        const data = event.data;
        const outcome = Outcome.parse(JSON.parse(data));
        const newMessages = outcome.actions.flatMap((action) => {
          switch (action.type) {
            case 'print-text':
              return [action.text];
            case 'change-color':
              setColor(action.color);
              return [];
            default:
              return [];
          }
        })


        setMessages(oldMessages => [...oldMessages, ...newMessages]);
      };
    }
  }, [color]);

  
  const sendMessage = (message: string) => {
    if (webSocketRef.current) {
      webSocketRef.current.send(message);
      setMessages(prevMessages => [...prevMessages, message]);
    } else {
      console.error('WebSocket is not connected');
    }
  };
    
  return (
    <>
      { messages.map((message, index) => (
        <div key={index} className="message">
          {message}
        </div>
      )) }
      <div>
        <input 
          type="text"
          style={{ borderColor: color }}
          value={ draftMessage }
          onChange={ (event) => setDraftMessage(event.target.value) }
          onKeyDown={ (event) => { 
            if (event.key === 'Enter') {
              sendMessage(draftMessage);
              setDraftMessage('');
            } 
          } }>
        </input>
      </div>
    </>
  )
}

export default App
