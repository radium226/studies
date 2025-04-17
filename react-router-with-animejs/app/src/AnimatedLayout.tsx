import { motion } from 'motion/react';
import { ReactNode } from 'react';

const pageVariants = {
    initial: {
      opacity: 0
    },
    in: {
      opacity: 1
    },
    out: {
      opacity: 0
    }
  };

type AnimatedLayoutProps = {
    children: ReactNode;
};

const AnimatedLayout = ({ children }: AnimatedLayoutProps) => {
    return (
        <motion.div
            initial="hidden"
            animate="enter"
            exit="exit"
            variants={pageVariants}
            transition={{ duration: 0.5, type: 'easeInOut' }}
        >
            {children}
        </motion.div>
    );
};

export default AnimatedLayout;