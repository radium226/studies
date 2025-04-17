import { useRef, PropsWithChildren, useEffect } from "react";
import { animate } from "animejs";


export type WiggleProps = PropsWithChildren & {
}

export default function Wiggle({ children }: WiggleProps) {
    const divRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const divElement = divRef.current;
        if (divElement != null) {
            console.log("TOPTOPT")
            const animation = animate(
                divElement,
                {
                    rotate: [-20, 0, 20, 0, -20],
                    easing: "easeInOutQuad",
                    duration: 1000,
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