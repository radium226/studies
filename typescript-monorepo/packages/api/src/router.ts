import { Router } from 'express';
import { User } from '@repo/models';

export function createRouter(): Router {
  const router = Router();

  router.get('/users', (request, response) => {
    const users: User[] = [
        {
            email: 'foo@bar.foobar',
            name: 'Foo Bar',
        },
        {
          email: 'jean@michel.jarr',
          name: 'Jean Michel Jarr',
        },
    ];
    
    response.json([
        ...users,
    ])
  });

  return router;
}