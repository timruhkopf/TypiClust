import csv
from pathlib import Path


class BufferedFileLogger:
    def __init__(
            self,
            file_name,
            file_path='.',
            buffer_size=1000,
            header=("metric", "value", "global_step")
    ):
        self.file_path = Path(file_path)
        self.file_path.mkdir(parents=True, exist_ok=True)
        self.file_name = file_name
        self.buffer_size = buffer_size
        self.buffer = []

        self.file = open(
            self.file_path / self.file_name,
            mode='w',
            newline='',
            buffering=1  # Line buffering
        )
        self.writer = csv.writer(self.file)
        # Write the header of the CSV file
        self.writer.writerow(header)

    def add_scalar(self, *args):
        self.buffer.append(args)
        if len(self.buffer) >= self.buffer_size:
            self._flush()

    def _flush(self):
        if self.buffer:
            self.writer.writerows(self.buffer)
            self.buffer = []

    def close(self):
        self._flush()
        self.file.close()


if __name__ == '__main__':
    import numpy as np

    # Assuming BufferedFileLogger is already defined as per your code snippet

    # Initialize the logger
    logger = BufferedFileLogger(
        file_name='training_metrics.csv', buffer_size=10,
        header=("metric", "value", "global_step", "some_value"))

    # Simulate training process
    num_epochs = 100
    for epoch in range(num_epochs):
        # Simulate some metrics (in practice, you'd compute these)
        loss = np.random.random()  # Simulated loss value
        accuracy = np.random.random()  # Simulated accuracy value

        # Log metrics (using global_step as epoch number)
        logger.add_scalar('loss', loss, epoch, 1)
        logger.add_scalar('accuracy', accuracy, epoch, 1)

    # Close the logger after training
    logger.close()
