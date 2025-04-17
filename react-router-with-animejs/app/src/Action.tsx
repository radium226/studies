import { Link, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';

import Pulse from "./Pulse";
import Wiggle from "./Wiggle";


export type ActionProps = {
    no: string;
};


export default function Action({ no }: ActionProps) {
    const navigate = useNavigate();

    useEffect(() => {
        const timeoutHandle = setTimeout(() => {
            navigate(`/${parseInt(no) + 1}`);
        }, 5000);
        return () => {
            clearTimeout(timeoutHandle);
        };
    }, [no, navigate]);

    const emojis = [
        "🛬",
        "🚆",
    ]
    
    return (
        <div className="flex h-full justify-center items-center text-4xl">
            <div className="text-center">
                { parseInt(no) % 2 == 0 ? <Pulse>
                    <Link to={ `/${parseInt(no) + 1}` }>{ emojis[parseInt(no) % emojis.length] }</Link>
                </Pulse> : <Wiggle>
                    <Link to={ `/${parseInt(no) + 1}` }>{ emojis[parseInt(no) % emojis.length] }</Link>
                </Wiggle> }
                
                <div className="text-2xl font-bold text-sky-200">
                    { parseInt(no) % 2 == 0 ? "Let's go! " : "Here we go! " }
                </div>
            </div>
        </div>
    );
};