CREATE DATABASE engine_proof CHARACTER SET utf8mb4;
USE engine_proof;

CREATE TABLE products (
    id INT PRIMARY KEY,
    name VARCHAR(30) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE sales (
    id INT PRIMARY KEY,
    product_id INT NOT NULL,
    quantity INT NOT NULL
) ENGINE=DuckDB;

INSERT INTO products VALUES (1, 'Notebook'), (2, 'Pen');
INSERT INTO sales VALUES (1, 1, 2), (2, 2, 5), (3, 1, 1);
