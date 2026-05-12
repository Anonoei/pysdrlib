from pysdrlib import File

def main():
    dev = File()
    dev.set_path("data/fm_rds_250k_1Msamples.iq")
    dev.set_fmt("cf64")
    # dev.set_sample_rate(250_000)
    dev.open()

    for data in dev(4096):
        print(f"Got data! {data.shape}")

    dev.close()


if __name__ == "__main__":
    main()
