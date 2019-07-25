import time
import matplotlib.pyplot as plt

import src.problemgenerator.array as array
import src.problemgenerator.filters as filters


def main():
    d = {"tar": 1, "rat": 1.1, "range": 1}
    img_path = "demo/landscape.png"

    # Use the vectorized version
    data = plt.imread(img_path)
    x_node = array.Array(data.shape)
    s = filters.SaturationVectorized("tar", "rat", "range")
    x_node.addfilter(s)
    start = time.time()
    result = x_node.generate_error(data, d)
    end = time.time()
    print(f"Time vectorized: {end-start}")

    plt.imshow(result)
    plt.show()


if __name__ == "__main__":
    main()
