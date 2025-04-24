import { useState, useEffect } from 'react'
import { User } from '@repo/models';
import { UserList } from '@repo/components';
import { listUsers } from './client';
import './App.css'

function App() {
  const [users, setUsers] = useState<User[]>([])

  useEffect(() => {
    async function go() {
      try {
        const users = await listUsers();
        setUsers(users);
      } catch (error) {
        console.error('Error fetching users:', error);
      }
    }

    go();
  }, [])

  return (
    <UserList users={users} />
  )
}

export default App
