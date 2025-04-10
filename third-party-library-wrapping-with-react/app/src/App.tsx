import { 
  PropsWithChildren, 
  useRef, 
  createContext, 
  useContext, 
  useCallback, 
  useState, 
  useEffect, 
  useMemo,
  useId,
  Children,
  isValidElement
} from "react";


const thirdPartyLib = (rootElement: HTMLElement, words: string[]) => {
  console.log(`[thirdPartyLib] words=${words}`);
  words.forEach((word) => {
    const divElement = document.createElement('div');
    const wordTextNode = document.createTextNode(word);
    divElement.appendChild(wordTextNode);
    rootElement.appendChild(divElement);
  });
};

/*
<ThirdParty>
  <Word value='foo' />
  <Word value='bar' />
</ThirdParty>
*/

type Registry = {
  register: (key: string, word: string) => void;
  unregister: (key: string) => void;
}

const RegistryContext = createContext<Registry>({
  register: (key: string, word: string) => { },
  unregister: () => {},
});

type ThirdPartyProps = PropsWithChildren & {};

function ThirdParty({ children }: ThirdPartyProps) {
  const [wordsByKey, setWordsByKey] = useState<{[ key: string ]: string}>({});

  const divRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const divElement = divRef.current;
    if (divElement != null) {
      const words = Object.values(wordsByKey);
      console.log(`[ThirdParty] Calling thirdPartyLib with words=${words}...`);
      thirdPartyLib(divElement, words);
    }

    return () => {
      if (divElement !== null) {
        while (divElement.firstChild) {
          divElement.removeChild(divElement.lastChild!);
        }
      }
    }
  }, [wordsByKey]);
  
  const registry = useMemo(() => ({
    register: (key: string, word: string) => {
      setWordsByKey((oldWordsByKey) => {
        return {
          [key]: word,
          ...oldWordsByKey,
        };
      });
    },
    unregister: (keyToUnregister: string) => {
      setWordsByKey((oldWordsByKey) => {
        return Object
          .entries(oldWordsByKey)
          .filter(([key, _]) => key !== keyToUnregister)
          .reduce((newWordsByKey, [key, word]) => { 
            return {
              ...newWordsByKey,
              [key]: word,
            }
          }, 
          {});
      });
    },
  }), []);
  

  return (
    <RegistryContext.Provider value={ registry }>
      <div ref={ divRef }>{
        Children.map(children, (child) => {
          if (isValidElement(child)) {
            if (child.type === Word) {
              return child;
            }
          }
          
          console.warn(`[ThirdParty] Invalid child type: ${child}`);
          return null;
        })
      } </div>
    </RegistryContext.Provider>
  );
}

type WordProps = { value: string };

function Word({ value }: WordProps) {
  const registry = useContext(RegistryContext);
  const key = useId();
  useEffect(() => {
    console.log(`[Word] Registering... (key=${key})`);
    registry.register(key, value);
    console.log
    return () => {
      console.log(`[Word] Unregistering... (key=${key})`);
      registry.unregister(key);
      console.log(`[Word] Unregistered! (key=${key})`);
    };
  }, [registry]);
  return null;
}

export default function App() {
  return (
    <ThirdParty>
      <Word value="foo" />
      <Word value="tata" />
      <Word value="dfd" />
      { "flhdfkjhd " }
      <Word value="f2r2" />
      <Word value="toto" />
    </ThirdParty>
  );
}
