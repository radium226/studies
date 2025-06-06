import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'

import CreateRecipePage from './pages/createRecipe/CreateRecipePage'
import { BotProvider, useBotContext } from './services/bot/botContext'

function BotConversation() {
  const { bot } = useBotContext()

  return (
    <div>
      <button onClick={() => bot.generateRecipe()}>Generate Recipe</button>
    </div>
  );
}


function App() {
  return (
    <BotProvider>
      <CreateRecipePage />
      <BotConversation />
    </BotProvider>
  )
}

export default App
