import { User } from '@repo/models';
import { z } from 'zod';


export async function listUsers(): Promise<User[]> {
    const response = await fetch('http://localhost:3000/users');
    if (!response.ok) {
        throw new Error(`Failed to fetch users: ${response.statusText}`);
    }
    const data = await response.json();
    const users = z.array(User).parse(data);
    return users;
}