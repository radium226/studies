import { useLocation } from 'react-router';

import { useEffect, useState, useRef } from 'react';
import { Feedback, Action } from './feedback';


export type BotProps = {

    onAction?: (action: Action) => void;
    backgrounColor?: string;

}


export default function Bot({ onAction, backgrounColor }: BotProps) {
    const webSocketRef = useRef<WebSocket | null>(null);

    const [messages, setMessages] = useState<string[]>([]);

    const [draftMessage, setDraftMessage] = useState('');

    const { pathname } = useLocation();


    useEffect(() => {
        webSocketRef.current = new WebSocket(`ws://${window.location.hostname}:8000/ws`);
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
                const feedback = Feedback.parse(JSON.parse(data));
                const message = feedback.message;
                if (message !== null) {
                    setMessages(oldMessages => [...oldMessages, message]);
                }
                
                feedback.actions.forEach(action => {
                    if (onAction) {
                        onAction(action);
                    }
                });
            };
        }
    }, []);


    const sendMessage = (message: string) => {
        if (webSocketRef.current) {
            const question = JSON.stringify({ location: pathname, message });
            webSocketRef.current.send(question);
            setMessages(prevMessages => [...prevMessages, message]);
        } else {
            console.error('WebSocket is not connected');
        }
    };

    const conversationDivRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (conversationDivRef.current) {
            conversationDivRef.current.scrollTop = conversationDivRef.current.scrollHeight;
        }
    }, [messages]);


    return (
        <div
            ref={conversationDivRef}
            className={ `fixed bottom-4 right-4 w-80 h-50 z-50 ${backgrounColor} text-white p-4 rounded-lg shadow-lg overflow-y-auto` }
        >
            {messages.map((message, index) => (
                <div key={index} className={ `mb-2 p-2 ${backgrounColor} rounded` }>
                    {message}
                </div>
            ))}
            <input
                type="text"
                className="w-full p-2 rounded"
                placeholder="Type a message..."
                value={draftMessage}
                onChange={ (event) => setDraftMessage(event.target.value) }
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        setDraftMessage('');
                        sendMessage(draftMessage);
                    }
                }}
            />
        </div>
    )

}