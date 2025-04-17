import { useLocation, Outlet, useOutlet } from "react-router-dom"
import { animate, createScope, Scope } from "animejs";
import { useEffect, useRef, useState, ReactElement, cloneElement, use } from 'react';
import AnimatedOutlet from "./AnimatedOutlet";



export type IndexProps = {
    
}


enum TransitionState {
    Initial = 0,
    Before,
    Ongoing,
    After
}


export default function Layout({ }: IndexProps) {
    const divRef = useRef<HTMLDivElement>(null);
    const scopeRef = useRef<Scope>(null);

    

    const newOutlet = useOutlet();
    const [oldOutlet, setOldOutlet] = useState<ReactElement | null>(newOutlet);
    
    const [transitionState, setTransitionState] = useState(TransitionState.Initial);
    
    const { pathname: newPath } = useLocation();
    const [oldPath, setOldPath] = useState<string>(newPath);

    useEffect(() => {
        if ((oldPath != newPath)) {
            setTransitionState(TransitionState.Before);
        }
    }, [newPath, oldPath]);

    const slide = (onComplete: () => void) => {
        const divElement = divRef.current;
        if (divElement != null) {
            scopeRef.current = createScope({ root: divElement })
                .add( scope => {
                    const divElement = divRef.current
                    if (divElement !== null) {
                        const animation = animate(
                            divElement,
                            {
                                translateX: "-50%",
                                easing: "linear",
                                duration: 250,
                                loop: false,
                                onComplete: () => {
                                    onComplete();
                                }
                            }
                        )
            
                        return () => {}
                    }
                })
        }
    }

    useEffect(() => {
        // if (transitionState === TransitionState.After) {
        //     console.log("Appearing... ")
        //     makeAppear(() => {
        //         console.log("Appeared! ");
        //     });
        // }

        if (transitionState === TransitionState.Before) {
            setTransitionState(TransitionState.Ongoing);
            slide(() => {
                setTransitionState(TransitionState.After);
                setOldOutlet(newOutlet);
                setOldPath(newPath);
            });
        }

        return () => {
        }
      }, [transitionState]);

    useEffect(() => {
        if (transitionState == TransitionState.After) {
            const scope = scopeRef.current;
            if (scope != null) {
                scope.revert()
            }
        }
    })

    const children = (
        <div ref={ divRef } className="slider w-[200%] h-[100%] flex justify-center items-center">
            <div className="h-full w-full h-full flex-col">
                { oldOutlet }
            </div>
            <div className="h-full w-full h-full flex-col">
                { transitionState == TransitionState.After ? null : newOutlet}
            </div>
        </div>
    );

    return (
        <div className="h-[250px] w-[500px] bg-sky-400 overflow-hidden">
            { children }
        </div>
    );
}