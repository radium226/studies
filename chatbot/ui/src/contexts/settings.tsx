import { createContext, useState, useContext } from "react";


export const SettingsContext = createContext<{
  email?: string;
  setEmail?: (email: string) => void;
}>({
  email: undefined,
  setEmail: undefined,
});


export function SettingsProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [email, setEmail] = useState<string | undefined>(undefined);

  return (
    <SettingsContext.Provider value={{ email, setEmail }}>
      { children }
    </SettingsContext.Provider>
  );
}

export function useSettings() {
  const context = useContext(SettingsContext);
  return context;
}

export function useEmail(): [string | undefined, (email: string) => void] {
  const context = useContext(SettingsContext);
  if (!context.setEmail) {
    throw new Error("useUpdateEmail must be used within a SettingsProvider");
  }
  return [context.email, context.setEmail];
}