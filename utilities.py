

def cut_and_paste_down( batch, dim=1) :
    return batch.transpose(0,1).reshape(-1)

def cut_and_paste_up( batch, dim=1, beam_size) :
        '''batch.size = [batch_size*beam_size, z]
           return size = [batch_size,z*beam_size]'''
    return batch.reshape(beam_size,-1,batch.shape[1]).transpose(0,1).reshape(-1,beam_size*batch.shape[1])

def convert_mask_to_inf( mask):
    mask[mask==0] = -np.inf
    mask[mask==1] = 0
    return mask


def infs_to_zero(self,mask) :
    mask[mask==0]=1
    mask[mask==-np.inf] = 0
    return mask

class clone_batch() :

    def __init__(self, n, pll_dat=True) :
        super().__init__()
        self.n = n
        self.pll_dat = pll_dat

    def transform_xlm_in(self, sample) :
        '''Obtains all possible samples from 1 sample
           and returns 'sample' with content,position_ids
           and langs of size [self.n, z*self.n]
           of form (if self.n=3 and z=4 and sample['input_ids']=[abcd]) :-
           sample['input_ids'].t():- [[abcd00000000],
                                    [0000abcd0000],
                                    [00000000abcd]]'''
        l = ['X', 'Y'] if self.pll_dat else ['X']
        for key in l :
            z = len(sample[key]['input_ids'])
            for subkey in sample[key] :
                if subkey != 'lengths' :
                    sample[key][subkey] = torch.stack([torch.cat([torch.zeros((i*z)), sample[key][subkey], torch.zeros(((self.n-i-1)*z))])
                                                for i in range(self.n)]).t()
        return sample


    def get_xlm__att_mask(self, batch) :
        '''If input :- [[abcd00000000],
                        [0000abcd0000],
                        [00000000abcd],other samples]
              output:- [[111100000000],
                        [000011110000],
                        [000000001111], similarly for other samples]'''
        max_size = batch['lengths'].max()
        att_mask = []
        for elem in batch['lengths'] :
            #self.n elements corres. to 'elem' length
            att_mask.append( torch.stack([torch.cat([torch.zeros((i*elem)), torch.ones((elem)), torch.zeros((max_size-(i+1)*elem))])
                                                for i in range(self.n)]) )
        return torch.cat(att_mask)



