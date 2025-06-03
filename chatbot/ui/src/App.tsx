import { BrowserRouter, Routes, Route } from "react-router"

import Layout from "./Layout"

import Welcome from "./pages/Welcome";
import Settings from "./pages/Settings";

import { SettingsProvider } from "./contexts/settings";


export type AppProps = {
}


export default function App() {
    return (
        <SettingsProvider>
            <BrowserRouter>
                <Routes>
                    <Route element={ <Layout /> }>
                        <Route path="/" element={ <Welcome /> } />
                        <Route path="/settings" element={ <Settings /> } />
                    </Route>
                </Routes>
            </BrowserRouter>
        </SettingsProvider>
    )

}