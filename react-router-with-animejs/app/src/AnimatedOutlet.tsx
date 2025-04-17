import { useLocation, useOutlet } from 'react-router-dom';
import { AnimatePresence } from 'motion/react';
import { cloneElement } from 'react';


export default function AnimatedOutlet() {
    const location = useLocation(); // provided by react-router-dom
    const element = useOutlet(); // provided by react-router-dom

    return (
        <AnimatePresence mode="wait" initial={true}>
            {element && cloneElement(element, { key: location.pathname })}
        </AnimatePresence>
    );
};
