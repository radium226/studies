import './App.css'
import { doCreateRouter } from './router'
import { RouterProvider } from '@tanstack/react-router'
import { Bot } from './spi'
import { useRef } from 'react'


function App() {
  const botRef = useRef<Bot | null>(null);
  botRef.current = botRef.current ?? new Bot();

  
  return (
    <RouterProvider router={ doCreateRouter(botRef.current) } />
  )
}

export default App
