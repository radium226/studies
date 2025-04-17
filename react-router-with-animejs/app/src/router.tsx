import { createBrowserRouter, useParams } from "react-router-dom";
import Layout from "./Layout.tsx";
import Action from "./Action.tsx";
import AnimatedLayout from "./AnimatedLayout.tsx";


const router = createBrowserRouter([
    {
        path: "/",
        Component: Layout,
        children: [
            {
                path: "/:actionNo",
                Component: () => {
                    const { actionNo } = useParams();
                    return (
                        <Action no={actionNo!} />
                    );
                }
            },
        ],
    },
]);


export default router;