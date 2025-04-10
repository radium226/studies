import { 
  PropsWithChildren, 
  useRef, 
  createContext, 
  useContext, 
  useState, 
  useEffect, 
  useMemo,
  useId,
} from "react";

import Silhouette from "./assets/silhouette.svg";
import { SVGInjector } from "@tanem/svg-injector";


type Key = string;

type Transformer = (svgElement: SVGElement) => void;

type Registry = {
  register: (keyToRegister: Key, transformer: Transformer) => void;
  unregister: (keyToUnregister: Key) => void;
}

const RegistryContext = createContext<Registry>({
  register: (keyToRegister: Key, transformer: Transformer) => { },
  unregister: () => {},
});

type BodyProps = PropsWithChildren & {

  src: string;

};

function Body({ src, children }: BodyProps) {
  const [transformersByKey, setTransformersByKey] = useState<{[ key: Key ]: Transformer}>({});

  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const divElement = ref.current;
    if (divElement !== null) {
      const svgElement = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svgElement.setAttribute("width", "100vw");
      svgElement.setAttribute("height", "100vh");
      divElement.appendChild(svgElement);
      svgElement.setAttribute("data-src", src);
      SVGInjector(
        svgElement, 
        {
          beforeEach(svgElement: SVGElement) {
            (svgElement.querySelector(".silhouette") as SVGElement).style.fill = "lightGray";
            svgElement.querySelectorAll(".bodyParts [data-bodyPart]")
               .forEach((element) => {
                 element.setAttribute("fill", "gray");
               });

            const transformers = Object.values(transformersByKey);
            transformers.forEach((transformer) => transformer(svgElement));
          }
        }
      );
    }

    return () => {
      if (divElement !== null) {
        while (divElement.firstChild) {
          divElement.removeChild(divElement.lastChild!);
        }
      }
    }
  }, [transformersByKey]);
  
  const registry = useMemo(() => ({
    register: (keyToRegister: Key, transformer: Transformer) => {
      setTransformersByKey((oldTransformersByKey) => {
        return {
          [keyToRegister]: transformer,
          ...oldTransformersByKey,
        };
      });
    },
    unregister: (keyToUnregister: Key) => {
      setTransformersByKey((oldTransformersByKey) => {
        return Object
          .entries(oldTransformersByKey)
          .filter(([key, _]) => key !== keyToUnregister)
          .reduce((newTransformersByKey, [key, transformer]) => { 
            return {
              ...newTransformersByKey,
              [key]: transformer,
            }
          }, 
          {});
      });
    },
  }), []);
  

  return (
    <RegistryContext.Provider value={ registry }>
      <div style={ {
        "width": "100%",
        "height": "100%",
      }} ref={ ref }>{ children }</div>
    </RegistryContext.Provider>
  );
}

type BodyPartProps = { transformer: Transformer }

function BodyPart({ transformer }: BodyPartProps) {
  const registry = useContext(RegistryContext);
  const key = useId();
  useEffect(() => {
    console.log(`[Word] Registering... (key=${key})`);
    registry.register(key, transformer);
    console.log
    return () => {
      console.log(`[Word] Unregistering... (key=${key})`);
      registry.unregister(key);
      console.log(`[Word] Unregistered! (key=${key})`);
    };
  }, [registry]);
  return null;
}

export type InsideOrOutsideProps = {
  color: string;
}


export function createBodyPart(name: string) {
  return ({ color }: { color: string }) => {
    return (
      <BodyPart transformer={(svgElement) =>
        Array.from(svgElement.querySelectorAll(`[data-bodyPart="${name}"]`))
          .map((element) => element as SVGElement)
          .forEach((element) => {
            element.style.fill = color;
          })
      } />
    );
  };
}

export const Biceps = createBodyPart("biceps");
export const Triceps = createBodyPart("triceps");
export const Obliques = createBodyPart("obliques");
export const Abs = createBodyPart("abs");

export default function App() {
  return (
    <Body src={ Silhouette }>
      <Biceps color="red" />
    </Body>
  );
}
