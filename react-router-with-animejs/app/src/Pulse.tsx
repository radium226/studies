import { useRef, PropsWithChildren, useEffect } from "react";
import { animate } from "animejs";


export type PulseProps = PropsWithChildren & {

}

export default function Pulse({ children }: PulseProps) {
    const divRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const divElement = divRef.current;
        if (divElement != null) {
            const animation = animate(
                divElement,
                {
                    scale: [1, 1.5, 1],
                    easing: "easeInOutQuad",
                    duration: 500,
                    loop: true,
                }
            )
            return () => {
                animation.reset(); 
            }
        }
    }, []);
    
    return (
        <div ref={ divRef }>
            { children }
        </div>
    );
}